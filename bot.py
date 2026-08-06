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
from typing import Optional

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
import safety
import scheduler
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
    "🪪 /who — your Telegram id and who else has access\n"
    "❓ /help — this message\n\n"
    "*Sharing* (owner only)\n"
    "`/allow <id> [name]` lets someone else in · `/revoke <id>` takes it back\n\n"
    "_The buttons answer straight from the database, so they're free._"
)


# ---------------------------------------------------------------------------
# Controlo de acesso
#
# Um bot de Telegram é público: qualquer pessoa que descubra o seu username
# pode escrever-lhe. O porteiro é a única coisa entre isso e os dados.
#
# Três regras, todas deliberadas:
#
#   1. **Silêncio absoluto.** Quem não está na lista não recebe resposta
#      nenhuma — nem sequer uma recusa. Uma recusa confirma que o bot existe,
#      que está ligado e que tem dono; e é uma mensagem que qualquer estranho
#      podia mandar o bot enviar as vezes que quisesse.
#   2. **Nada de reclamar o bot pela conversa.** Só o dono autoriza ids novos.
#      A lista arranca vazia e vazia continua até alguém a preencher pelo
#      `.env` ou pelo painel de controlo.
#   3. **Na dúvida, fechado.** Uma actualização sem utilizador identificável
#      (uma publicação num canal, por exemplo) é descartada, não deixada
#      passar. Era esta a porta das traseiras: `effective_user` vem a None nos
#      canais e o porteiro devolvia o controlo em vez de interromper, pelo que
#      qualquer pessoa que metesse o bot num canal seu ficava com os comandos
#      todos à mão — incluindo o `/allow`.
# ---------------------------------------------------------------------------
# Cache em memória da lista da base de dados: o porteiro corre em cada
# actualização e não vale a pena tocar no disco de cada vez.
_acesso_cache: set[int] = set()


def refresh_access_cache() -> set[int]:
    """Relê da base de dados quem está autorizado."""
    global _acesso_cache
    _acesso_cache = db.allowed_user_ids()
    return _acesso_cache


def autorizados() -> set[int]:
    """Lista efectiva: o `.env` manda; senão, a base de dados."""
    if settings.allowed_user_ids:
        return set(settings.allowed_user_ids)
    return _acesso_cache


def tem_acesso(user_id: Optional[int]) -> bool:
    """Única resposta à pergunta «esta pessoa pode usar o bot?»."""
    return user_id is not None and user_id in autorizados()


def e_dono(user_id: int) -> bool:
    """True se for o dono. Com a lista fixa no `.env` não há dono: ninguém
    pode alterar a lista a partir do Telegram, e é isso que interessa."""
    if settings.allowed_user_ids:
        return False
    return db.is_owner(user_id)


async def guard_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Corre antes de tudo o resto; descarta em silêncio o que não é de casa."""
    user = update.effective_user

    if user is None:
        # Sem remetente não há forma de autorizar seja o que for. Publicações
        # em canais, mensagens anónimas e afins acabam aqui.
        chat = update.effective_chat
        logger.warning(
            "Actualização sem utilizador descartada (chat %s, tipo %s).",
            getattr(chat, "id", "?"),
            getattr(chat, "type", "?"),
        )
        raise ApplicationHandlerStop

    if tem_acesso(user.id):
        return

    # Nem resposta, nem base de dados tocada: só uma linha no registo, com o
    # id à vista para o dono o poder autorizar se quiser.
    logger.warning(
        "Acesso recusado a %s (id %s, @%s) — sem resposta.",
        safety.para_registo(user.first_name or "?", safety.MAX_NOME),
        user.id,
        safety.para_registo(user.username or "sem username", safety.MAX_NOME),
    )
    raise ApplicationHandlerStop


# ---------------------------------------------------------------------------
# Utilitários
# ---------------------------------------------------------------------------
def _context_from(update: Update) -> ToolContext:
    """Extrai o utilizador e o chat da actualização recebida.

    O nome é limpo aqui porque vai parar ao prompt de sistema: é escolhido
    pela própria pessoa e sem isto podia levar mudanças de linha — ou seja,
    instruções novas — lá para dentro.
    """
    user = update.effective_user
    chat = update.effective_chat
    return ToolContext(
        user_id=user.id if user else 0,
        chat_id=chat.id if chat else 0,
        first_name=safety.limpar_nome(user.first_name if user else ""),
    )


def _comprimento_telegram(texto: str) -> int:
    """Comprimento como o Telegram o conta: unidades UTF-16, não caracteres.

    Um emoji fora do plano básico ocupa duas unidades. Contar caracteres do
    Python deixava passar blocos com o dobro do tamanho permitido, e a
    mensagem era recusada — o que, nas listas de notas, bastava para partir o
    `/notes` de quem usasse emojis.
    """
    return len(texto.encode("utf-16-le")) // 2


def _split_message(text: str) -> list[str]:
    """Parte respostas longas em blocos aceites pelo Telegram."""
    if _comprimento_telegram(text) <= TELEGRAM_MAX_LENGTH:
        return [text]

    blocks: list[str] = []
    remaining = text
    while _comprimento_telegram(remaining) > TELEGRAM_MAX_LENGTH:
        # Procuramos o maior prefixo que caiba, medido em unidades UTF-16.
        limite = TELEGRAM_MAX_LENGTH
        while _comprimento_telegram(remaining[:limite]) > TELEGRAM_MAX_LENGTH:
            limite -= (_comprimento_telegram(remaining[:limite]) - TELEGRAM_MAX_LENGTH + 1) // 2 or 1

        cut = remaining.rfind("\n", 0, limite)
        if cut <= 0:
            cut = limite
        blocks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    if remaining:
        blocks.append(remaining)
    return blocks


async def send_text(bot, chat_id: int, text: str, reply_markup=None) -> None:
    """Envia texto tentando Markdown e caindo para texto simples se falhar.

    O modelo pode gerar asteriscos ou underscores desemparelhados, o que faz o
    Telegram rejeitar a mensagem — nesse caso reenviamos sem formatação. O
    reenvio também pode falhar (o `BadRequest` cobre outros motivos além da
    formatação), por isso vai igualmente protegido: não enviar uma mensagem
    nunca pode derrubar o handler que a estava a compor.
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
            try:
                await bot.send_message(chat_id=chat_id, text=block, reply_markup=markup)
            except BadRequest:
                logger.warning(
                    "Não foi possível enviar um bloco de %d caracteres para o chat %s.",
                    len(block),
                    chat_id,
                    exc_info=True,
                )


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
# Gestão de acesso
# ---------------------------------------------------------------------------
async def cmd_who(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mostra o teu id e quem mais tem acesso."""
    ctx = _context_from(update)

    linhas = [f"🪪 Your Telegram id is `{ctx.user_id}`.\n"]
    if settings.allowed_user_ids:
        linhas.append("*Access list* (from `ALLOWED_USER_IDS` in .env)")
        linhas += [f"• `{uid}`" for uid in sorted(settings.allowed_user_ids)]
        linhas.append("\n_Managed in the .env file, not with_ `/allow`.")
    else:
        entradas = await asyncio.to_thread(db.list_access)
        if entradas:
            linhas.append("*Access list*")
            for entrada in entradas:
                # A etiqueta foi escrita por outra pessoa: escapada, senão um
                # nome como `[toca aqui](https://falso)` chegava ao dono como
                # um link a sério, com a credibilidade do bot por trás.
                etiqueta = safety.neutralizar_markdown(entrada["label"] or "unnamed")
                marca = " 👑 owner" if entrada["is_owner"] else ""
                linhas.append(f"• `{entrada['user_id']}` — {etiqueta}{marca}")
            if e_dono(ctx.user_id):
                linhas.append("\n_Use_ `/allow <id> [name]` _or_ `/revoke <id>`_._")
        else:
            linhas.append(
                "⚠️ *Nobody is authorised yet.*\n"
                "_Add the first id from the Windows panel («Utilizadores») or "
                "set_ `ALLOWED_USER_IDS` _in the .env file._"
            )

    await send_text(context.bot, ctx.chat_id, "\n".join(linhas))


async def _recusar_se_nao_for_dono(ctx: ToolContext, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Devolve True (e explica-se) se quem chamou não for o dono."""
    if e_dono(ctx.user_id):
        return False
    await send_text(
        context.bot,
        ctx.chat_id,
        "Only the owner can change who has access. 🔒",
    )
    logger.warning(
        "Utilizador %s tentou gerir acessos sem ser dono.", ctx.user_id
    )
    return True


async def cmd_allow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Autoriza outra pessoa a usar o bot."""
    ctx = _context_from(update)

    if settings.allowed_user_ids:
        await send_text(
            context.bot,
            ctx.chat_id,
            "Access is pinned by `ALLOWED_USER_IDS` in the .env file — "
            "edit it there and restart.",
        )
        return

    if await _recusar_se_nao_for_dono(ctx, context):
        return

    if not context.args:
        await send_text(
            context.bot,
            ctx.chat_id,
            "Usage: `/allow <telegram id> [name]`\n\n"
            "_They can find their id by sending_ `/who` _to any bot that shows it — "
            "or have them message me once and read the id from the logs._",
        )
        return

    try:
        novo_id = int(context.args[0])
    except ValueError:
        await send_text(
            context.bot,
            ctx.chat_id,
            f"`{safety.neutralizar_markdown(context.args[0])}` is not a numeric id.",
        )
        return

    etiqueta = safety.limitar(" ".join(context.args[1:]), safety.MAX_ETIQUETA)
    await asyncio.to_thread(db.grant_access, novo_id, etiqueta, False)
    refresh_access_cache()
    logger.info("Utilizador %s autorizado por %s.", novo_id, ctx.user_id)
    sufixo = f" ({safety.neutralizar_markdown(etiqueta)})" if etiqueta else ""
    await send_text(context.bot, ctx.chat_id, f"✅ `{novo_id}`{sufixo} can now talk to me.")


async def cmd_revoke(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Retira o acesso a alguém (o dono não pode ser retirado)."""
    ctx = _context_from(update)

    if settings.allowed_user_ids:
        await send_text(
            context.bot,
            ctx.chat_id,
            "Access is pinned by `ALLOWED_USER_IDS` in the .env file — "
            "edit it there and restart.",
        )
        return

    if await _recusar_se_nao_for_dono(ctx, context):
        return

    if not context.args:
        await send_text(context.bot, ctx.chat_id, "Usage: `/revoke <telegram id>`")
        return

    try:
        alvo = int(context.args[0])
    except ValueError:
        await send_text(
            context.bot,
            ctx.chat_id,
            f"`{safety.neutralizar_markdown(context.args[0])}` is not a numeric id.",
        )
        return

    # Os jobs já agendados têm de sair do scheduler à mão; a base de dados
    # marca os lembretes como disparados, mas o job vive noutro sítio.
    pendentes = await asyncio.to_thread(db.pending_reminder_ids, alvo)
    removido = await asyncio.to_thread(db.revoke_access, alvo)
    refresh_access_cache()
    if removido:
        for reminder_id in pendentes:
            scheduler.cancel_reminder(reminder_id)
        logger.info(
            "Acesso retirado a %s por %s (%d lembrete(s) cancelado(s)).",
            alvo, ctx.user_id, len(pendentes),
        )
        await send_text(context.bot, ctx.chat_id, f"✅ `{alvo}` can no longer talk to me.")
    else:
        await send_text(
            context.bot,
            ctx.chat_id,
            f"`{alvo}` isn't on the list — or is the owner, who can't be removed.",
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
    if not update.message or not update.message.text:
        return
    handler = _BUTTON_ROUTES.get(update.message.text.strip())
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
    # O corpo da mensagem só vai para o registo se LOG_MESSAGES estiver ligado:
    # o ficheiro fica em claro no disco e as notas guardam justamente códigos e
    # palavras-passe. Mesmo aí vai saneado — sem isto, uma mensagem com
    # mudanças de linha escrevia linhas inteiras falsas no `assistente.log`.
    if settings.log_messages:
        logger.info(
            "Mensagem de %s (%s): %s",
            safety.para_registo(ctx.first_name, safety.MAX_NOME),
            ctx.user_id,
            safety.para_registo(texto),
        )
    else:
        logger.info("Mensagem de %s (%d caracteres).", ctx.user_id, len(texto))

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
    """Liga todos os handlers à aplicação do Telegram.

    Tudo aqui é restrito a conversas privadas. Os dados são pessoais e o
    `chat_id` é guardado com cada lembrete: num grupo, um `/notes` despejava as
    notas à frente de toda a gente e um lembrete marcado lá era entregue lá,
    horas depois, quando já ninguém se lembrava disso.
    """
    # Grupo -1 corre antes de todos os outros: é o porteiro.
    application.add_handler(TypeHandler(Update, guard_access), group=-1)

    privado = filters.ChatType.PRIVATE

    for nomes, funcao in [
        ("start", cmd_start),
        (["help", "ajuda"], cmd_help),
        (["today", "hoje"], cmd_today),
        ("agenda", cmd_agenda),
        (["notes", "notas"], cmd_notes),
        (["reminders", "lembretes"], cmd_reminders),
        (["forget", "esquecer"], cmd_forget),
        (["who", "whoami", "quem"], cmd_who),
        ("allow", cmd_allow),
        ("revoke", cmd_revoke),
    ]:
        application.add_handler(CommandHandler(nomes, funcao, filters=privado))

    # Os botões têm de ser interceptados ANTES do handler genérico de texto,
    # senão o seu conteúdo seguia para o modelo e custava tokens.
    botoes = "|".join(re.escape(rotulo) for rotulo in _BUTTON_ROUTES)
    application.add_handler(
        MessageHandler(privado & filters.Regex(f"^({botoes})$"), handle_button)
    )

    application.add_handler(
        MessageHandler(privado & filters.TEXT & ~filters.COMMAND, handle_message)
    )
    application.add_handler(
        MessageHandler(
            privado
            & (filters.VOICE | filters.AUDIO | filters.PHOTO | filters.Document.ALL | filters.VIDEO),
            handle_unsupported,
        )
    )

    application.add_error_handler(on_error)
    logger.info("Handlers do Telegram registados (só conversas privadas).")
