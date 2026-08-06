"""Ferramentas que o modelo DeepSeek pode invocar (function calling).

Cada ferramenta é uma função Python normal que devolve um dicionário
serializável em JSON. O esquema em `TOOL_SCHEMAS` segue exactamente o formato
de *tools* da OpenAI, que a API DeepSeek reproduz.

Nota sobre a língua: tudo o que viaja para o modelo — descrições das
ferramentas e chaves dos resultados — está em inglês, por duas razões. Gasta
menos tokens do que o português (que usa acentos e palavras mais longas) e o
catálogo de ferramentas é reenviado em *todas* as chamadas. Os comentários e a
documentação ficam em português, porque esses só nós é que os lemos.

O contexto do utilizador (quem fala e em que chat) não vem do modelo — seria
inseguro — mas de um `ToolContext` construído pelo handler do Telegram.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

import dateparser

import database as db
import scheduler
from config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Contexto
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ToolContext:
    """Identifica o utilizador em nome de quem as ferramentas são executadas."""

    user_id: int
    chat_id: int
    first_name: str = ""


# ---------------------------------------------------------------------------
# Datas
# ---------------------------------------------------------------------------
_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

_MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

# Expressões que o dateparser não interpreta bem. Mantemos as portuguesas
# porque continuam a funcionar mesmo com o bot a responder em inglês.
_NORMALISATIONS: list[tuple[str, str]] = [
    (r"\bmeio-?\s*dia\b", "12:00"),
    (r"\bmeia-?\s*noite\b", "00:00"),
    (r"\bao\s+almo[çc]o\b", "13:00"),
    (r"\bao\s+jantar\b", "20:00"),
    (r"\bde\s+manh[ãa]\b", "09:00"),
    (r"\bda\s+manh[ãa]\b", ""),
    (r"\b[àa]\s+tarde\b", "15:00"),
    (r"\bda\s+tarde\b", ""),
    (r"\b[àa]\s+noite\b", "21:00"),
    (r"\bda\s+noite\b", ""),
    (r"\b(\d{1,2})\s*h\s*(\d{2})\b", r"\1:\2"),   # 15h30 -> 15:30
    (r"\b(\d{1,2})\s*h\b", r"\1:00"),             # 15h   -> 15:00
    (r"\b(\d{1,2})\s*horas?\b", r"\1:00"),        # 15 horas -> 15:00
]

# Expressões relativas ("in 20 minutes", "daqui a 20 minutos") são calculadas
# directamente: o dateparser não as cobre bem em português e a normalização
# acima transformaria "2 horas" em "2:00", alterando o sentido da frase.
_NUMBER_WORDS: dict[str, float] = {
    # Português
    "um": 1, "uma": 1, "dois": 2, "duas": 2, "três": 3, "tres": 3,
    "quatro": 4, "cinco": 5, "seis": 6, "sete": 7, "oito": 8, "nove": 9,
    "dez": 10, "onze": 11, "doze": 12, "quinze": 15, "vinte": 20,
    "trinta": 30, "meia": 0.5, "meio": 0.5,
    # Inglês
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "half": 0.5,
}

_UNIT_SECONDS: dict[str, float] = {
    # Português
    "segundos": 1, "segundo": 1, "seg": 1,
    "minutos": 60, "minuto": 60,
    "horas": 3600, "hora": 3600,
    "dias": 86400, "dia": 86400,
    "semanas": 604800, "semana": 604800,
    "meses": 2592000, "mês": 2592000, "mes": 2592000,
    "anos": 31536000, "ano": 31536000,
    # Inglês
    "seconds": 1, "second": 1, "secs": 1, "sec": 1,
    "minutes": 60, "minute": 60, "mins": 60, "min": 60,
    "hours": 3600, "hour": 3600, "hrs": 3600, "hr": 3600,
    "days": 86400, "day": 86400,
    "weeks": 604800, "week": 604800,
    "months": 2592000, "month": 2592000,
    "years": 31536000, "year": 31536000,
    # Abreviaturas de uma letra (por último, para não roubarem os prefixos)
    "h": 3600, "m": 60, "s": 1,
}


def _alternation(palavras) -> str:
    """Alternância de regex com as palavras mais longas primeiro.

    O `re` devolve a primeira alternativa que encaixar, por isso "minutes" tem
    de ser testado antes de "min" e de "m".
    """
    return "|".join(re.escape(p) for p in sorted(palavras, key=len, reverse=True))


_RELATIVE_RE = re.compile(
    r"\b(?:daqui\s+a|d'?aqui\s+a|dentro\s+de|passad[oa]s?|em|in|within|after)\s+"
    r"(?P<quantia>\d+(?:[.,]\d+)?|" + _alternation(_NUMBER_WORDS) + r")\s*"
    r"(?P<unidade>" + _alternation(_UNIT_SECONDS) + r")\b"
)


def _parse_relative(text: str, now: datetime) -> Optional[datetime]:
    """Interpreta «in 20 minutes» / «daqui a 2 horas» / «dentro de 3 dias»."""
    match = _RELATIVE_RE.search(text.lower())
    if not match:
        return None

    quantia_raw = match.group("quantia")
    try:
        quantia = float(quantia_raw.replace(",", "."))
    except ValueError:
        quantia = _NUMBER_WORDS.get(quantia_raw, 0)
    if quantia <= 0:
        return None

    return now + timedelta(seconds=quantia * _UNIT_SECONDS[match.group("unidade")])


def _normalise_date_text(text: str) -> str:
    """Limpa e uniformiza expressões temporais antes da análise."""
    cleaned = text.strip().lower()
    for pattern, replacement in _NORMALISATIONS:
        cleaned = re.sub(pattern, replacement, cleaned)
    # "às"/"as" antes de uma hora só confunde o parser. A verificação de que
    # se segue um número evita mutilar frases em inglês ("as soon as").
    cleaned = re.sub(r"\b[àa]s\b(?=\s*\d)", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def parse_datetime(text: str, prefer_future: bool = True) -> Optional[datetime]:
    """Converte texto em linguagem natural num `datetime` com fuso horário.

    Devolve None quando não é possível interpretar a expressão.
    """
    if not text or not text.strip():
        return None

    now = datetime.now(settings.tzinfo)

    # 1) Expressões relativas têm tratamento próprio (ver `_parse_relative`).
    relativo = _parse_relative(text, now)
    if relativo is not None:
        return relativo

    # 2) Restantes casos: dateparser, primeiro sobre o texto normalizado.
    parser_settings: dict[str, Any] = {
        "TIMEZONE": settings.timezone,
        "TO_TIMEZONE": settings.timezone,
        "RETURN_AS_TIMEZONE_AWARE": True,
        "PREFER_DATES_FROM": "future" if prefer_future else "current_period",
        "DATE_ORDER": "DMY",  # convenção europeia: 03/04 = 3 de Abril
        "RELATIVE_BASE": now.replace(tzinfo=None),
    }

    for candidate in (_normalise_date_text(text), text.strip()):
        if not candidate:
            continue
        parsed = dateparser.parse(candidate, languages=["en", "pt"], settings=parser_settings)
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=settings.tzinfo)
            return parsed.astimezone(settings.tzinfo)
    return None


def format_datetime(value: datetime | str) -> str:
    """Formata uma data/hora para leitura humana: `Friday, 7 August 2026 at 15:00`."""
    dt = _coerce_datetime(value)
    if dt is None:
        return str(value)
    return (
        f"{_WEEKDAYS[dt.weekday()]}, {dt.day} {_MONTHS[dt.month - 1]} {dt.year} "
        f"at {dt:%H:%M}"
    )


def format_short(value: datetime | str) -> str:
    """Formato compacto: `07/08/2026 15:00`."""
    dt = _coerce_datetime(value)
    return dt.strftime("%d/%m/%Y %H:%M") if dt else str(value)


def format_time(value: datetime | str) -> str:
    """Só as horas: `15:00`."""
    dt = _coerce_datetime(value)
    return dt.strftime("%H:%M") if dt else str(value)


def to_datetime(value: datetime | str) -> Optional[datetime]:
    """Converte um valor ISO (ou datetime) num `datetime` com fuso garantido."""
    return _coerce_datetime(value)


def _coerce_datetime(value: datetime | str) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=settings.tzinfo)
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=settings.tzinfo)


def _day_bounds(day: datetime) -> tuple[datetime, datetime]:
    """Devolve o início e o fim (exclusivo) do dia de `day`."""
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def _serialise_event(event: dict[str, Any]) -> dict[str, Any]:
    """Reduz um evento aos campos úteis para o modelo."""
    return {
        "id": event["id"],
        "description": event["description"],
        "when": format_datetime(event["event_time"]),
    }


def _serialise_note(note: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": note["id"],
        "content": note["content"],
        "saved": format_short(note["created_at"]),
    }


# ---------------------------------------------------------------------------
# Ferramenta 1 — data e hora actuais
# ---------------------------------------------------------------------------
def get_current_datetime(ctx: ToolContext) -> dict[str, Any]:
    """Devolve a data e a hora actuais no fuso configurado."""
    now = datetime.now(settings.tzinfo)
    return {"status": "ok", "now": format_datetime(now), "timezone": settings.timezone}


# ---------------------------------------------------------------------------
# Ferramenta 2 — criar evento (+ lembrete automático)
# ---------------------------------------------------------------------------
def add_event(ctx: ToolContext, date: str, description: str) -> dict[str, Any]:
    """Guarda um compromisso e agenda o lembrete prévio."""
    description = (description or "").strip()
    if not description:
        return {"status": "error", "error": "Missing description. Ask the user what the appointment is."}

    event_time = parse_datetime(date)
    if event_time is None:
        return {
            "status": "error",
            "error": f"Could not understand the date {date!r}. Ask for a clearer one.",
        }

    now = datetime.now(settings.tzinfo)
    event_id = db.create_event(ctx.user_id, ctx.chat_id, description, event_time)

    # Lembrete automático, por omissão 15 minutos antes.
    reminder_info: dict[str, Any] = {"created": False}
    if event_time > now:
        lead = timedelta(minutes=settings.event_reminder_lead_minutes)
        remind_at = event_time - lead
        antecedencia_normal = True
        if remind_at <= now:
            # Compromisso muito próximo: avisa daqui a instantes, desde que
            # ainda seja antes da hora do evento.
            candidate = now + timedelta(seconds=60)
            remind_at = candidate if candidate < event_time else event_time
            antecedencia_normal = False

        message = f"{description}\n\n🗓️ {format_datetime(event_time)}"
        if antecedencia_normal:
            message += f"\n(in {settings.event_reminder_lead_minutes} minutes)"

        reminder_id = db.create_reminder(
            user_id=ctx.user_id,
            chat_id=ctx.chat_id,
            message=message,
            remind_at=remind_at,
            kind="event",
            event_id=event_id,
        )
        scheduler.schedule_reminder(reminder_id, remind_at)
        reminder_info = {"created": True, "at": format_datetime(remind_at)}

    logger.info("Evento #%s criado para o utilizador %s.", event_id, ctx.user_id)
    return {
        "status": "ok",
        "event": {
            "id": event_id,
            "description": description,
            "when": format_datetime(event_time),
        },
        "reminder": reminder_info,
        "in_the_past": event_time <= now,
    }


# ---------------------------------------------------------------------------
# Ferramenta 3 — procurar eventos
# ---------------------------------------------------------------------------
def search_events(ctx: ToolContext, query: str = "") -> dict[str, Any]:
    """Procura eventos por data (natural) ou por palavra-chave.

    Estratégia: se a consulta se parecer com uma data, devolve os eventos desse
    dia; caso contrário procura no texto. Uma consulta vazia devolve os
    próximos compromissos.
    """
    query = (query or "").strip()

    if not query:
        events = db.get_upcoming_events(ctx.user_id, datetime.now(settings.tzinfo))
        return {
            "status": "ok",
            "matched_by": "upcoming",
            "count": len(events),
            "events": [_serialise_event(event) for event in events],
        }

    # 1) Tentativa por data. `prefer_future=False` evita que "today" salte para
    #    amanhã e permite consultar dias passados.
    day = parse_datetime(query, prefer_future=False)
    if day is not None:
        start, end = _day_bounds(day)
        events = db.get_events_between(ctx.user_id, start, end)
        if events or _looks_like_date(query):
            return {
                "status": "ok",
                "matched_by": "date",
                "day": format_short(start).split(" ")[0],
                "count": len(events),
                "events": [_serialise_event(event) for event in events],
            }

    # 2) Pesquisa textual.
    events = db.search_events_by_text(ctx.user_id, query)
    return {
        "status": "ok",
        "matched_by": "text",
        "count": len(events),
        "events": [_serialise_event(event) for event in events],
    }


_DATE_HINTS = (
    # Português
    "hoje", "amanhã", "amanha", "ontem", "próxima", "proxima", "segunda",
    "terça", "terca", "quarta", "quinta", "sexta", "sábado", "sabado",
    "domingo", "semana", "mês", "mes", "janeiro", "fevereiro", "março",
    "marco", "abril", "maio", "junho", "julho", "agosto", "setembro",
    "outubro", "novembro", "dezembro",
    # Inglês
    "today", "tomorrow", "yesterday", "next", "this", "monday", "tuesday",
    "wednesday", "thursday", "friday", "saturday", "sunday", "week", "month",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
)


def _looks_like_date(query: str) -> bool:
    """Heurística: a consulta refere-se claramente a uma data?"""
    lowered = query.lower()
    if re.search(r"\d{1,2}[/\-.]\d{1,2}", lowered) or re.fullmatch(r"\d{1,2}\s*\w*", lowered):
        return True
    return any(hint in lowered for hint in _DATE_HINTS)


# ---------------------------------------------------------------------------
# Ferramenta 4 — guardar nota
# ---------------------------------------------------------------------------
def save_note(ctx: ToolContext, content: str) -> dict[str, Any]:
    """Guarda uma nota com data/hora."""
    content = (content or "").strip()
    if not content:
        return {"status": "error", "error": "Empty note. Ask what should be saved."}

    note = db.create_note(ctx.user_id, content)
    logger.info("Nota #%s criada para o utilizador %s.", note["id"], ctx.user_id)
    return {"status": "ok", "note": _serialise_note(note)}


# ---------------------------------------------------------------------------
# Ferramenta 5 — procurar notas
# ---------------------------------------------------------------------------
def search_notes(ctx: ToolContext, query: str = "") -> dict[str, Any]:
    """Procura notas por texto; sem termo devolve as mais recentes."""
    query = (query or "").strip()
    notes = db.list_notes(ctx.user_id) if not query else db.search_notes_by_text(ctx.user_id, query)
    return {
        "status": "ok",
        "count": len(notes),
        "notes": [_serialise_note(note) for note in notes],
    }


# ---------------------------------------------------------------------------
# Ferramenta 6 — lembrete simples
# ---------------------------------------------------------------------------
def set_reminder(ctx: ToolContext, message: str, time: str) -> dict[str, Any]:
    """Agenda um lembrete pontual para uma hora indicada."""
    message = (message or "").strip()
    if not message:
        return {"status": "error", "error": "Missing reminder text."}

    remind_at = parse_datetime(time)
    if remind_at is None:
        return {
            "status": "error",
            "error": f"Could not understand the time {time!r}. Ask for a clearer one.",
        }

    now = datetime.now(settings.tzinfo)
    if remind_at <= now:
        # Horas soltas ("9:00") que já passaram referem-se ao dia seguinte.
        if now - remind_at < timedelta(days=1):
            remind_at += timedelta(days=1)
        else:
            return {
                "status": "error",
                "error": f"{format_datetime(remind_at)} is in the past. Ask for a future time.",
            }

    reminder_id = db.create_reminder(
        user_id=ctx.user_id,
        chat_id=ctx.chat_id,
        message=message,
        remind_at=remind_at,
        kind="simple",
    )
    scheduler.schedule_reminder(reminder_id, remind_at)
    logger.info("Lembrete #%s criado para o utilizador %s.", reminder_id, ctx.user_id)

    return {
        "status": "ok",
        "reminder": {"id": reminder_id, "message": message, "at": format_datetime(remind_at)},
    }


# ---------------------------------------------------------------------------
# Ferramenta 7 — lembretes pendentes
# ---------------------------------------------------------------------------
def list_reminders(ctx: ToolContext) -> dict[str, Any]:
    """Lista os lembretes que ainda não dispararam."""
    reminders = db.get_user_reminders(ctx.user_id)
    return {
        "status": "ok",
        "count": len(reminders),
        "reminders": [
            {
                "id": reminder["id"],
                "message": reminder["message"].splitlines()[0],
                "at": format_datetime(reminder["remind_at"]),
            }
            for reminder in reminders
        ],
    }


# ---------------------------------------------------------------------------
# Ferramenta 8 — apagar
#
# Um único `delete_item` para as três entidades, em vez de três ferramentas
# separadas: poupa cerca de 120 tokens em cada chamada à API e ao modelo é
# indiferente.
# ---------------------------------------------------------------------------
def _cancel_event_reminders(event_id: int) -> None:
    """Cancela os jobs dos lembretes de um evento antes de este desaparecer."""
    for reminder in db.get_reminders_for_event(event_id):
        scheduler.cancel_reminder(reminder["id"])


def delete_item(ctx: ToolContext, kind: str, id: int) -> dict[str, Any]:  # noqa: A002
    """Apaga um evento, uma nota ou um lembrete pelo respectivo id."""
    kind = (kind or "").strip().lower()
    try:
        item_id = int(id)
    except (TypeError, ValueError):
        return {"status": "error", "error": f"Invalid id {id!r}."}

    if kind == "event":
        # A base de dados apaga os lembretes em cascata, mas os jobs já
        # agendados têm de ser retirados do scheduler à mão.
        _cancel_event_reminders(item_id)
        apagado = db.delete_event(ctx.user_id, item_id)
    elif kind == "note":
        apagado = db.delete_note(ctx.user_id, item_id)
    elif kind == "reminder":
        scheduler.cancel_reminder(item_id)
        apagado = db.delete_reminder(ctx.user_id, item_id)
    else:
        return {"status": "error", "error": "kind must be event, note or reminder."}

    if not apagado:
        return {"status": "error", "error": f"No {kind} with id {item_id}. Search for it first."}

    logger.info("%s #%s apagado pelo utilizador %s.", kind, item_id, ctx.user_id)
    return {"status": "ok", "deleted": {"kind": kind, "id": item_id}}


# ---------------------------------------------------------------------------
# Ferramenta 9 — remarcar / corrigir um evento
# ---------------------------------------------------------------------------
def update_event(
    ctx: ToolContext, id: int, date: str = "", description: str = ""  # noqa: A002
) -> dict[str, Any]:
    """Muda a hora e/ou a descrição de um evento, reagendando o aviso."""
    try:
        event_id = int(id)
    except (TypeError, ValueError):
        return {"status": "error", "error": f"Invalid id {id!r}."}

    evento = db.get_event(event_id)
    if evento is None or evento["user_id"] != ctx.user_id:
        return {"status": "error", "error": f"No event with id {event_id}. Search for it first."}

    nova_hora = None
    if date and date.strip():
        nova_hora = parse_datetime(date)
        if nova_hora is None:
            return {"status": "error", "error": f"Could not understand the date {date!r}."}

    nova_descricao = description.strip() or None
    if nova_hora is None and nova_descricao is None:
        return {"status": "error", "error": "Nothing to change: give a new date, a new description, or both."}

    db.update_event(ctx.user_id, event_id, nova_descricao, nova_hora)

    hora_final = nova_hora or to_datetime(evento["event_time"])
    descricao_final = nova_descricao or evento["description"]

    # O aviso antigo deixou de fazer sentido: cancelamos e criamos outro.
    for reminder in db.get_reminders_for_event(event_id):
        scheduler.cancel_reminder(reminder["id"])
        db.delete_reminder(ctx.user_id, reminder["id"])

    reminder_info: dict[str, Any] = {"created": False}
    now = datetime.now(settings.tzinfo)
    if hora_final > now:
        remind_at = hora_final - timedelta(minutes=settings.event_reminder_lead_minutes)
        if remind_at <= now:
            candidate = now + timedelta(seconds=60)
            remind_at = candidate if candidate < hora_final else hora_final
        reminder_id = db.create_reminder(
            user_id=ctx.user_id,
            chat_id=ctx.chat_id,
            message=f"{descricao_final}\n\n🗓️ {format_datetime(hora_final)}",
            remind_at=remind_at,
            kind="event",
            event_id=event_id,
        )
        scheduler.schedule_reminder(reminder_id, remind_at)
        reminder_info = {"created": True, "at": format_datetime(remind_at)}

    logger.info("Evento #%s actualizado pelo utilizador %s.", event_id, ctx.user_id)
    return {
        "status": "ok",
        "event": {
            "id": event_id,
            "description": descricao_final,
            "when": format_datetime(hora_final),
        },
        "reminder": reminder_info,
    }


# ---------------------------------------------------------------------------
# Ferramenta 10 — preferências duradouras
# ---------------------------------------------------------------------------
def set_preference(ctx: ToolContext, key: str, value: str = "") -> dict[str, Any]:
    """Guarda (ou remove, com valor vazio) uma preferência do utilizador."""
    key = (key or "").strip()
    if not key:
        return {"status": "error", "error": "Missing preference name."}

    value = (value or "").strip()
    if not value:
        db.delete_preference(ctx.user_id, key)
        return {"status": "ok", "removed": key}

    db.set_preference(ctx.user_id, key, value)
    logger.info("Preferência %r guardada para o utilizador %s.", key, ctx.user_id)
    return {"status": "ok", "preference": {key: value}}


# ---------------------------------------------------------------------------
# Contexto de memória (usado na construção do bloco volátil do prompt)
# ---------------------------------------------------------------------------
def get_daily_context(user_id: int) -> str:
    """Resumo curto do dia, injectado no prompt para respostas mais directas."""
    now = datetime.now(settings.tzinfo)
    start, end = _day_bounds(now)
    today = db.get_events_between(user_id, start, end)

    if not today:
        return "Nothing scheduled today."

    linhas = [f"- {format_time(event['event_time'])} {event['description']}" for event in today]
    return "Today: " + "; ".join(linha[2:] for linha in linhas)


# ---------------------------------------------------------------------------
# Esquemas no formato OpenAI / DeepSeek
#
# São reenviados em TODAS as chamadas à API, por isso cada palavra aqui é paga
# vezes sem conta. As descrições foram reduzidas ao mínimo que preserva a
# distinção entre ferramentas — sobretudo entre `add_event` (compromisso com
# hora marcada) e `set_reminder` (aviso solto).
# ---------------------------------------------------------------------------
TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_current_datetime",
            "description": "Current date and time. Call before reasoning about relative dates.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_event",
            "description": (
                "Save a diary appointment (meeting, trip, dinner); also schedules an "
                "alert 15 min before. Use when the thing happens at a time."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "When. Natural language or ISO."},
                    "description": {"type": "string", "description": "Short description."},
                },
                "required": ["date", "description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_events",
            "description": "Look up the diary. Query: a date, a keyword, or empty for upcoming.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Date or keyword. May be empty."}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_note",
            "description": "Save a fact worth keeping: ideas, lists, preferences, anything undated.",
            "parameters": {
                "type": "object",
                "properties": {"content": {"type": "string", "description": "Note text."}},
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_notes",
            "description": "Search saved notes. Empty query returns the most recent.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Text to search."}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_reminder",
            "description": (
                "Schedule a one-off alert with no diary entry, e.g. 'remind me to take "
                "the pill at 9'. Use when nothing is being booked."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Text to send."},
                    "time": {"type": "string", "description": "When. Natural language or ISO."},
                },
                "required": ["message", "time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_reminders",
            "description": "List alerts that have not fired yet.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_item",
            "description": (
                "Delete an event, note or alert. Search first to get its id; if several "
                "match, ask which one."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["event", "note", "reminder"]},
                    "id": {"type": "integer", "description": "Id from a search result."},
                },
                "required": ["kind", "id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_event",
            "description": (
                "Move or rename an existing appointment, rescheduling its alert. "
                "Use for 'push the dentist to 4pm'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "Id from a search result."},
                    "date": {"type": "string", "description": "New date/time. Omit to keep."},
                    "description": {"type": "string", "description": "New description. Omit to keep."},
                },
                "required": ["id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_preference",
            "description": (
                "Persist a lasting preference about how to behave, e.g. name to use, "
                "tone, emoji. Empty value removes it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Short name, e.g. 'call_me'."},
                    "value": {"type": "string", "description": "Value. Empty to remove."},
                },
                "required": ["key", "value"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Despacho
# ---------------------------------------------------------------------------
_REGISTRY: dict[str, Callable[..., dict[str, Any]]] = {
    "get_current_datetime": get_current_datetime,
    "add_event": add_event,
    "search_events": search_events,
    "save_note": save_note,
    "search_notes": search_notes,
    "set_reminder": set_reminder,
    "list_reminders": list_reminders,
    "delete_item": delete_item,
    "update_event": update_event,
    "set_preference": set_preference,
}


def execute_tool(name: str, arguments: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """Executa uma ferramenta pelo nome, devolvendo sempre um dicionário.

    Erros nunca são propagados: viram um resultado de estado "error" que o
    modelo consegue explicar ao utilizador.
    """
    function = _REGISTRY.get(name)
    if function is None:
        logger.warning("Ferramenta desconhecida pedida pelo modelo: %s", name)
        return {"status": "error", "error": f"No such tool: {name}."}

    try:
        # Só os argumentos previstos são passados adiante — o modelo pode
        # inventar chaves extra e isso não deve rebentar a chamada.
        allowed = _allowed_arguments(name)
        kwargs = {key: value for key, value in (arguments or {}).items() if key in allowed}
        return function(ctx, **kwargs)
    except TypeError as exc:
        logger.warning("Argumentos inválidos para %s: %s", name, exc)
        return {"status": "error", "error": f"Bad arguments for {name}: {exc}"}
    except Exception as exc:  # noqa: BLE001 — resiliência é intencional aqui
        logger.exception("Erro ao executar a ferramenta %s.", name)
        return {"status": "error", "error": f"{name} failed: {exc}"}


def _allowed_arguments(name: str) -> set[str]:
    """Nomes de parâmetros declarados no esquema de uma ferramenta."""
    for schema in TOOL_SCHEMAS:
        if schema["function"]["name"] == name:
            return set(schema["function"]["parameters"].get("properties", {}))
    return set()
