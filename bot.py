"""Handlers do Telegram e ciclo principal de conversa.

Este módulo só trata da camada Telegram: recebe mensagens, constrói o
`ToolContext`, delega o raciocínio em `llm.generate_reply` e devolve a resposta.
Os comandos (`/hoje`, `/notas`, ...) respondem directamente a partir da base de
dados — são consultas determinísticas que não justificam gastar tokens.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import database as db
import llm
import tools
from config import settings
from llm import AssistantError
from tools import ToolContext

logger = logging.getLogger(__name__)

# Limite de caracteres por mensagem imposto pelo Telegram.
TELEGRAM_MAX_LENGTH = 4096

WELCOME = (
    "Olá, {nome}! 👋\n\n"
    "Sou o teu assistente pessoal. Fala comigo em linguagem natural — eu trato do resto:\n\n"
    "🗓️ *Agenda* — «marca dentista quinta às 15h» (aviso automático 15 min antes)\n"
    "⏰ *Lembretes* — «lembra-me de tomar o comprimido às 9:00»\n"
    "📝 *Notas* — «guarda que o wi-fi do escritório é Torre2024»\n"
    "🔎 *Consultas* — «o que tenho hoje?», «o que sabes sobre o carro?»\n\n"
    "Também converso sobre outros assuntos. Escreve /ajuda para veres os comandos."
)

HELP = (
    "*Comandos disponíveis*\n\n"
    "/hoje — compromissos de hoje\n"
    "/agenda — próximos compromissos\n"
    "/notas — notas mais recentes\n"
    "/lembretes — lembretes por disparar\n"
    "/esquecer — limpa a conversa recente da minha memória curta\n"
    "/ajuda — esta mensagem\n\n"
    "Mas não precisas de comandos: basta escreveres normalmente.\n"
    "_Exemplos:_ «amanhã às 10h tenho consulta», «o que tenho na sexta?», "
    "«apontamento: o código do alarme é 4471»."
)


# ---------------------------------------------------------------------------
# Utilitários
# ---------------------------------------------------------------------------
def _context_from(update: Update) -> ToolContext:
    """Extrai o utilizador e o chat da actualização recebida."""
    user = update.effective_user
    chat = update.effective_chat
    return ToolContext(
        user_id=user.id if user else 0,
        chat_id=chat.id if chat else 0,
        first_name=(user.first_name if user else "") or "",
    )


def _split_message(text: str) -> list[str]:
    """Parte respostas longas em blocos aceites pelo Telegram."""
    if len(text) <= TELEGRAM_MAX_LENGTH:
        return [text]

    blocks: list[str] = []
    remaining = text
    while len(remaining) > TELEGRAM_MAX_LENGTH:
        cut = remaining.rfind("\n", 0, TELEGRAM_MAX_LENGTH)
        if cut <= 0:
            cut = TELEGRAM_MAX_LENGTH
        blocks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    if remaining:
        blocks.append(remaining)
    return blocks


async def send_text(bot, chat_id: int, text: str) -> None:
    """Envia texto tentando Markdown e caindo para texto simples se falhar.

    O modelo pode gerar asteriscos ou underscores desemparelhados, o que faz o
    Telegram rejeitar a mensagem — nesse caso reenviamos sem formatação.
    """
    for block in _split_message(text):
        try:
            await bot.send_message(chat_id=chat_id, text=block, parse_mode=ParseMode.MARKDOWN)
        except BadRequest as exc:
            logger.debug("Markdown rejeitado (%s); a reenviar em texto simples.", exc)
            await bot.send_message(chat_id=chat_id, text=block)


# ---------------------------------------------------------------------------
# Comandos
# ---------------------------------------------------------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _context_from(update)
    await send_text(context.bot, ctx.chat_id, WELCOME.format(nome=ctx.first_name or "olá"))


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _context_from(update)
    await send_text(context.bot, ctx.chat_id, HELP)


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Lista os compromissos do dia corrente."""
    ctx = _context_from(update)
    now = datetime.now(settings.tzinfo)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    events = db.get_events_between(ctx.user_id, start, start + timedelta(days=1))

    if not events:
        await send_text(
            context.bot,
            ctx.chat_id,
            "Hoje tens a agenda livre. 🎉\nQueres que marque alguma coisa?",
        )
        return

    linhas = [f"🗓️ *Hoje* — {tools.format_datetime(now).split(' às ')[0]}\n"]
    for event in events:
        hora = tools.format_short(event["event_time"])[11:]
        passado = "✅ " if tools.to_datetime(event["event_time"]) < now else "• "
        linhas.append(f"{passado}*{hora}* — {event['description']}")
    await send_text(context.bot, ctx.chat_id, "\n".join(linhas))


async def cmd_agenda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Lista os próximos compromissos."""
    ctx = _context_from(update)
    events = db.get_upcoming_events(ctx.user_id, datetime.now(settings.tzinfo), limit=10)

    if not events:
        await send_text(
            context.bot,
            ctx.chat_id,
            "Não tens compromissos futuros registados. Queres marcar algum?",
        )
        return

    linhas = ["🗓️ *Próximos compromissos*\n"]
    linhas += [
        f"• *{tools.format_short(event['event_time'])}* — {event['description']}"
        for event in events
    ]
    await send_text(context.bot, ctx.chat_id, "\n".join(linhas))


async def cmd_notes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mostra as notas mais recentes."""
    ctx = _context_from(update)
    notes = db.list_notes(ctx.user_id, limit=10)

    if not notes:
        await send_text(
            context.bot,
            ctx.chat_id,
            "Ainda não tens notas guardadas. Diz-me «guarda que...» e eu aponto. 📝",
        )
        return

    linhas = ["📝 *Notas recentes*\n"]
    linhas += [
        f"• {note['content']}\n  _{tools.format_short(note['created_at'])}_" for note in notes
    ]
    await send_text(context.bot, ctx.chat_id, "\n".join(linhas))


async def cmd_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mostra os lembretes ainda por disparar."""
    ctx = _context_from(update)
    reminders = db.get_user_reminders(ctx.user_id, limit=15)

    if not reminders:
        await send_text(context.bot, ctx.chat_id, "Não tens lembretes pendentes. ⏰")
        return

    linhas = ["⏰ *Lembretes pendentes*\n"]
    for reminder in reminders:
        etiqueta = "🗓️" if reminder["kind"] == "event" else "⏰"
        primeira_linha = reminder["message"].splitlines()[0]
        linhas.append(
            f"{etiqueta} *{tools.format_short(reminder['remind_at'])}* — {primeira_linha}"
        )
    await send_text(context.bot, ctx.chat_id, "\n".join(linhas))


async def cmd_forget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Limpa a memória de curto prazo (eventos, notas e resumos mantêm-se)."""
    ctx = _context_from(update)
    llm.reset_history(ctx.user_id)
    await send_text(
        context.bot,
        ctx.chat_id,
        "Feito — esqueci a conversa recente. 🧹\n"
        "_Os teus eventos, notas e lembretes continuam guardados._",
    )


# ---------------------------------------------------------------------------
# Conversa livre
# ---------------------------------------------------------------------------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Encaminha uma mensagem de texto para o modelo e devolve a resposta."""
    if not update.message or not update.message.text:
        return

    ctx = _context_from(update)
    texto = update.message.text.strip()
    logger.info("Mensagem de %s (%s): %s", ctx.first_name, ctx.user_id, texto)

    await context.bot.send_chat_action(chat_id=ctx.chat_id, action=ChatAction.TYPING)

    try:
        # O cliente DeepSeek é síncrono: corre numa thread para não bloquear o loop.
        resposta = await asyncio.to_thread(llm.generate_reply, ctx, texto)
    except AssistantError as exc:
        await send_text(context.bot, ctx.chat_id, f"⚠️ {exc}")
        return
    except Exception:  # noqa: BLE001 — nunca deixar o handler rebentar
        logger.exception("Erro inesperado ao processar a mensagem do utilizador %s.", ctx.user_id)
        await send_text(
            context.bot,
            ctx.chat_id,
            "⚠️ Aconteceu um erro inesperado do meu lado. Já ficou registado — tenta novamente.",
        )
        return

    await send_text(context.bot, ctx.chat_id, resposta)


async def handle_unsupported(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Resposta educada a conteúdos ainda não suportados (voz, fotos, ficheiros)."""
    ctx = _context_from(update)
    await send_text(
        context.bot,
        ctx.chat_id,
        "Por agora só consigo ler mensagens de texto. 🙈\n"
        "_Áudio e imagens estão na lista de melhorias futuras._",
    )


# ---------------------------------------------------------------------------
# Erros
# ---------------------------------------------------------------------------
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler global: regista a excepção e avisa o utilizador, sem crashar."""
    logger.exception("Excepção não tratada no handler.", exc_info=context.error)

    if isinstance(update, Update) and update.effective_chat:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="⚠️ Ocorreu um erro a processar o teu pedido. Podes tentar de novo?",
            )
        except Exception:  # noqa: BLE001 — o aviso é best-effort
            logger.debug("Não foi possível avisar o utilizador do erro.")


# ---------------------------------------------------------------------------
# Registo
# ---------------------------------------------------------------------------
def register_handlers(application: Application) -> None:
    """Liga todos os handlers à aplicação do Telegram."""
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler(["ajuda", "help"], cmd_help))
    application.add_handler(CommandHandler("hoje", cmd_today))
    application.add_handler(CommandHandler("agenda", cmd_agenda))
    application.add_handler(CommandHandler("notas", cmd_notes))
    application.add_handler(CommandHandler("lembretes", cmd_reminders))
    application.add_handler(CommandHandler("esquecer", cmd_forget))

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(
        MessageHandler(
            filters.VOICE | filters.AUDIO | filters.PHOTO | filters.Document.ALL | filters.VIDEO,
            handle_unsupported,
        )
    )

    application.add_error_handler(on_error)
    logger.info("Handlers do Telegram registados.")
