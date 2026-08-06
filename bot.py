"""Handlers do Telegram e ciclo principal de conversa.

Este módulo só trata da camada Telegram: recebe mensagens, constrói o
`ToolContext`, delega o raciocínio em `llm.generate_reply` e devolve a resposta.

Os comandos (`/today`, `/notes`, ...) e os botões do menu respondem
directamente a partir da base de dados — são consultas determinísticas que não
justificam gastar tokens. É por isso que os botões existem: cada toque num
deles é uma consulta que custa zero.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta

from telegram import ReplyKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
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

# ---------------------------------------------------------------------------
# Menu de botões
#
# Um `ReplyKeyboardMarkup` fica fixo por cima da caixa de texto. Tocar num
# botão envia o respectivo texto como mensagem normal, por isso há um handler
# com filtro exacto (registado *antes* do handler genérico) que os intercepta e
# os trata como comandos — sem nunca chegarem ao modelo.
# ---------------------------------------------------------------------------
BTN_TODAY = "📅 Today"
BTN_AGENDA = "🗓️ Agenda"
BTN_NOTES = "📝 Notes"
BTN_REMINDERS = "⏰ Reminders"
BTN_HELP = "❓ Help"

MENU = ReplyKeyboardMarkup(
    [[BTN_TODAY, BTN_AGENDA], [BTN_NOTES, BTN_REMINDERS], [BTN_HELP]],
    resize_keyboard=True,
    is_persistent=True,
    input_field_placeholder="Tell me anything…",
)

WELCOME = (
    "Hi {name}! 👋\n\n"
    "I'm your personal assistant. Just talk to me — no commands needed:\n\n"
    "🗓️ *Diary* — “dentist on Thursday at 3pm” (I'll alert you 15 min before)\n"
    "⏰ *Reminders* — “remind me to take the pill at 9”\n"
    "📝 *Notes* — “remember the office wifi is Torre2024”\n"
    "🔎 *Lookups* — “what's on today?”, “what do you know about the car?”\n\n"
    "The buttons below are instant lookups — and they cost nothing to run."
)

HELP = (
    "*What I can do*\n\n"
    "Just write naturally:\n"
    "• “lunch with Ana tomorrow at 1pm” → saved to the diary + alert\n"
    "• “remind me to call the garage at 5” → one-off alert\n"
    "• “note: the alarm code is 4471” → saved\n"
    "• “what's on Friday?” → looked up\n\n"
    "*Buttons and commands*\n"
    "📅 /today — today's appointments\n"
    "🗓️ /agenda — what's coming up\n"
    "📝 /notes — most recent notes\n"
    "⏰ /reminders — alerts not yet fired\n"
    "🧹 /forget — clear our recent chat from my short-term memory\n"
    "❓ /help — this message\n\n"
    "_The buttons answer straight from the database, so they're free._"
)


# ---------------------------------------------------------------------------
# Controlo de acesso
#
# Um bot de Telegram é público: qualquer pessoa que descubra o seu username
# pode escrever-lhe. Os dados estão isolados por `user_id` — um estranho nunca
# veria a agenda de outra pessoa — mas cada mensagem dele gastaria saldo da
# API. `ALLOWED_USER_IDS` fecha a porta.
# ---------------------------------------------------------------------------
async def guard_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Corre antes de tudo o resto; interrompe o processamento a estranhos."""
    if not settings.allowed_user_ids:
        return  # sem lista definida, o bot está aberto (avisado no arranque)

    user = update.effective_user
    if user is None or user.id in settings.allowed_user_ids:
        return

    logger.warning(
        "Acesso recusado a %s (id %s, @%s).",
        user.first_name or "?",
        user.id,
        user.username or "sem username",
    )
    chat = update.effective_chat
    if chat is not None:
        try:
            await context.bot.send_message(
                chat_id=chat.id,
                text="This is a private assistant and you're not on its list. 🔒",
            )
        except Exception:  # noqa: BLE001 — o aviso é best-effort
            logger.debug("Não foi possível avisar o utilizador não autorizado.")

    # Impede que qualquer outro handler veja esta actualização.
    raise ApplicationHandlerStop


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


async def send_text(bot, chat_id: int, text: str, reply_markup=None) -> None:
    """Envia texto tentando Markdown e caindo para texto simples se falhar.

    O modelo pode gerar asteriscos ou underscores desemparelhados, o que faz o
    Telegram rejeitar a mensagem — nesse caso reenviamos sem formatação.
    """
    blocks = _split_message(text)
    for indice, block in enumerate(blocks):
        # O teclado só vai na última parte, para não piscar entre blocos.
        markup = reply_markup if indice == len(blocks) - 1 else None
        try:
            await bot.send_message(
                chat_id=chat_id, text=block, parse_mode=ParseMode.MARKDOWN, reply_markup=markup
            )
        except BadRequest as exc:
            logger.debug("Markdown rejeitado (%s); a reenviar em texto simples.", exc)
            await bot.send_message(chat_id=chat_id, text=block, reply_markup=markup)


# ---------------------------------------------------------------------------
# Comandos
# ---------------------------------------------------------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _context_from(update)
    await send_text(
        context.bot, ctx.chat_id, WELCOME.format(name=ctx.first_name or "there"), reply_markup=MENU
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _context_from(update)
    await send_text(context.bot, ctx.chat_id, HELP, reply_markup=MENU)


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Lista os compromissos do dia corrente."""
    ctx = _context_from(update)
    now = datetime.now(settings.tzinfo)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    events = db.get_events_between(ctx.user_id, start, start + timedelta(days=1))

    if not events:
        await send_text(
            context.bot, ctx.chat_id, "Nothing on today. 🎉\nWant me to book something?"
        )
        return

    linhas = [f"📅 *Today* — {start:%d/%m/%Y}\n"]
    for event in events:
        marca = "✅" if tools.to_datetime(event["event_time"]) < now else "•"
        linhas.append(f"{marca} *{tools.format_time(event['event_time'])}* — {event['description']}")
    await send_text(context.bot, ctx.chat_id, "\n".join(linhas))


async def cmd_agenda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Lista os próximos compromissos."""
    ctx = _context_from(update)
    events = db.get_upcoming_events(ctx.user_id, datetime.now(settings.tzinfo), limit=10)

    if not events:
        await send_text(
            context.bot, ctx.chat_id, "Nothing coming up. Want to book something?"
        )
        return

    linhas = ["🗓️ *Coming up*\n"]
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
            context.bot, ctx.chat_id, "No notes yet. Say “remember that…” and I'll jot it down. 📝"
        )
        return

    linhas = ["📝 *Recent notes*\n"]
    linhas += [
        f"• {note['content']}\n  _{tools.format_short(note['created_at'])}_" for note in notes
    ]
    await send_text(context.bot, ctx.chat_id, "\n".join(linhas))


async def cmd_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mostra os lembretes ainda por disparar."""
    ctx = _context_from(update)
    reminders = db.get_user_reminders(ctx.user_id, limit=15)

    if not reminders:
        await send_text(context.bot, ctx.chat_id, "No pending alerts. ⏰")
        return

    linhas = ["⏰ *Pending alerts*\n"]
    for reminder in reminders:
        etiqueta = "🗓️" if reminder["kind"] == "event" else "⏰"
        primeira_linha = reminder["message"].splitlines()[0]
        linhas.append(
            f"{etiqueta} *{tools.format_short(reminder['remind_at'])}* — {primeira_linha}"
        )
    await send_text(context.bot, ctx.chat_id, "\n".join(linhas))


async def cmd_forget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Arruma e limpa a memória de curto prazo.

    Com o argumento `all`, apaga também a memória de longo prazo — útil quando
    os resumos guardados ficaram desactualizados (ou na língua errada).
    """
    ctx = _context_from(update)
    tudo = bool(context.args) and context.args[0].strip().lower() in {"all", "tudo"}

    if tudo:
        llm.reset_history(ctx.user_id)  # sem resumir: o objectivo é apagar
        apagados = await asyncio.to_thread(db.delete_summaries, ctx.user_id)
        await send_text(
            context.bot,
            ctx.chat_id,
            f"Wiped. 🧹 Recent chat *and* {apagados} stored memory summar"
            f"{'y' if apagados == 1 else 'ies'} are gone.\n"
            "_Your events, notes and alerts are untouched._",
        )
        return

    # Por omissão, resume antes de esquecer: nada do que foi dito se perde.
    await asyncio.to_thread(llm.flush_user, ctx.user_id)
    llm.reset_history(ctx.user_id)
    await send_text(
        context.bot,
        ctx.chat_id,
        "Done — recent chat cleared. 🧹\n"
        "_Anything worth remembering was filed away first; your events, notes "
        "and alerts are untouched._\n"
        "_Use_ `/forget all` _to wipe long-term memory too._",
    )


# ---------------------------------------------------------------------------
# Botões do menu
# ---------------------------------------------------------------------------
_BUTTON_ROUTES = {
    BTN_TODAY: cmd_today,
    BTN_AGENDA: cmd_agenda,
    BTN_NOTES: cmd_notes,
    BTN_REMINDERS: cmd_reminders,
    BTN_HELP: cmd_help,
}


async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Trata um toque no menu como se fosse o comando correspondente."""
    handler = _BUTTON_ROUTES.get((update.message.text or "").strip())
    if handler is not None:
        await handler(update, context)


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
            "⚠️ Something went wrong on my side. It's been logged — please try again.",
        )
        return

    await send_text(context.bot, ctx.chat_id, resposta)


async def handle_unsupported(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Resposta educada a conteúdos ainda não suportados (voz, fotos, ficheiros)."""
    ctx = _context_from(update)
    await send_text(
        context.bot,
        ctx.chat_id,
        "I can only read text for now. 🙈\n_Voice and images are on the roadmap._",
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
                text="⚠️ Something went wrong handling that. Mind trying again?",
            )
        except Exception:  # noqa: BLE001 — o aviso é best-effort
            logger.debug("Não foi possível avisar o utilizador do erro.")


# ---------------------------------------------------------------------------
# Registo
# ---------------------------------------------------------------------------
def register_handlers(application: Application) -> None:
    """Liga todos os handlers à aplicação do Telegram."""
    # Grupo -1 corre antes de todos os outros: é o porteiro.
    application.add_handler(TypeHandler(Update, guard_access), group=-1)

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler(["help", "ajuda"], cmd_help))
    application.add_handler(CommandHandler(["today", "hoje"], cmd_today))
    application.add_handler(CommandHandler("agenda", cmd_agenda))
    application.add_handler(CommandHandler(["notes", "notas"], cmd_notes))
    application.add_handler(CommandHandler(["reminders", "lembretes"], cmd_reminders))
    application.add_handler(CommandHandler(["forget", "esquecer"], cmd_forget))

    # Os botões têm de ser interceptados ANTES do handler genérico de texto,
    # senão o seu conteúdo seguia para o modelo e custava tokens.
    botoes = "|".join(re.escape(rotulo) for rotulo in _BUTTON_ROUTES)
    application.add_handler(MessageHandler(filters.Regex(f"^({botoes})$"), handle_button))

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(
        MessageHandler(
            filters.VOICE | filters.AUDIO | filters.PHOTO | filters.Document.ALL | filters.VIDEO,
            handle_unsupported,
        )
    )

    application.add_error_handler(on_error)
    logger.info("Handlers do Telegram registados.")
