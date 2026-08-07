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
from datetime import date, datetime, timedelta
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


# Um lembrete não faz sentido a mais de um século de distância, e acima disto
# o `timedelta` rebenta com OverflowError em vez de devolver uma data.
_MAX_SEGUNDOS_FUTURO = 100 * 366 * 86400


def _parse_relative(text: str, now: datetime) -> Optional[datetime]:
    """Interpreta «in 20 minutes» / «daqui a 2 horas» / «dentro de 3 dias».

    Devolve None — nunca levanta — para expressões absurdas («in 1e20 years»):
    quem chama espera um `Optional`, e um OverflowError a subir daqui aparecia
    ao utilizador como uma mensagem de erro interna do Python.
    """
    match = _RELATIVE_RE.search(text.lower())
    if not match:
        return None

    quantia_raw = match.group("quantia")
    try:
        quantia = float(quantia_raw.replace(",", "."))
    except (ValueError, OverflowError):
        quantia = _NUMBER_WORDS.get(quantia_raw, 0)
    if quantia <= 0:
        return None

    segundos = quantia * _UNIT_SECONDS[match.group("unidade")]
    if segundos > _MAX_SEGUNDOS_FUTURO:
        return None

    try:
        return now + timedelta(seconds=segundos)
    except (OverflowError, OSError, ValueError):
        return None


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


# Dias da semana nas duas línguas, para reconhecer «last thursday».
_DIAS_SEMANA = (
    "monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"segunda|ter[çc]a|quarta|quinta|sexta|s[áa]bado|domingo"
)

# «last thursday» e «quinta passada» dizem a mesma coisa que «thursday» num
# contexto que já prefere o passado — e o dateparser, com `en` e `pt` carregados
# ao mesmo tempo, devolve None para a forma com qualificador embora entenda a
# forma sem ele. Tiramos o qualificador, que aqui não acrescenta nada.
_PASSADO_REDUNDANTE: list[tuple[re.Pattern[str], str]] = [
    # «last thursday» / «última sexta» -> «thursday» / «sexta»
    (re.compile(rf"\b(?:last|past|[úu]ltim[oa]|passad[oa])\s+(?=(?:{_DIAS_SEMANA})\b)"), ""),
    # «quinta passada» -> «quinta» (o português põe o qualificador depois)
    (re.compile(rf"\b({_DIAS_SEMANA})\s+(?:passad[oa]|[úu]ltim[oa])\b"), r"\1"),
]


def parse_day(text: str) -> Optional[date]:
    """Interpreta texto que designa um **dia passado**, devolvendo só a data.

    Distinto do `parse_datetime`, e não por capricho: ali «quinta» quer dizer a
    próxima quinta (marca-se um compromisso para a frente), aqui quer dizer a
    quinta que passou (conta-se o que já aconteceu). Com a preferência trocada,
    contar ao domingo uma ida ao dentista «na quinta» arrumava-a quatro dias no
    futuro.

    Texto vazio significa hoje. Devolve None se não for possível interpretar.
    """
    hoje = datetime.now(settings.tzinfo).date()
    if not text or not text.strip():
        return hoje

    limpo = _normalise_date_text(text)
    for padrao, substituto in _PASSADO_REDUNDANTE:
        limpo = padrao.sub(substituto, limpo)
    limpo = re.sub(r"\s+", " ", limpo).strip()

    for candidate in (limpo, text.strip()):
        if not candidate:
            continue
        parsed = dateparser.parse(
            candidate,
            languages=["en", "pt"],
            settings={
                "TIMEZONE": settings.timezone,
                "TO_TIMEZONE": settings.timezone,
                "RETURN_AS_TIMEZONE_AWARE": False,
                "PREFER_DATES_FROM": "past",
                "DATE_ORDER": "DMY",
                "RELATIVE_BASE": datetime.now(settings.tzinfo).replace(tzinfo=None),
            },
        )
        if parsed is not None:
            return parsed.date()
    return None


def format_day(value: date | str) -> str:
    """Um dia por extenso: `Thursday, 7 August 2026`."""
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value)
        except ValueError:
            return str(value)
    return f"{_WEEKDAYS[value.weekday()]}, {value.day} {_MONTHS[value.month - 1]} {value.year}"


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
# Ferramentas 11 e 12 — linha do tempo (o que já aconteceu)
#
# A fronteira que interessa manter nítida:
#   * `add_event`   — vai acontecer, a uma hora marcada, e avisa.
#   * `save_note`   — um facto sem data («o código do alarme é 4471»).
#   * `log_moment`  — já aconteceu, num dia, e não avisa ninguém.
# ---------------------------------------------------------------------------
def _serialise_moment(moment: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": moment["id"],
        "content": moment["content"],
        "day": moment["happened_on"],
    }


def log_moment(ctx: ToolContext, content: str, date: str = "") -> dict[str, Any]:  # noqa: A002
    """Regista um acontecimento passado no dia em que aconteceu."""
    content = (content or "").strip()
    if not content:
        return {"status": "error", "error": "Nothing to record. Ask what happened."}

    dia = parse_day(date)
    if dia is None:
        return {
            "status": "error",
            "error": f"Could not understand the day {date!r}. Ask for a clearer one.",
        }

    moment = db.create_moment(ctx.user_id, content, dia)
    logger.info("Acontecimento #%s registado para o utilizador %s.", moment["id"], ctx.user_id)
    return {
        "status": "ok",
        "moment": {"id": moment["id"], "content": content, "day": format_day(dia)},
    }


def update_moment(
    ctx: ToolContext, id: int, content: str = "", date: str = ""  # noqa: A002
) -> dict[str, Any]:
    """Corrige o texto ou o dia de um acontecimento já registado.

    Sem isto, um pedido para corrigir uma entrada não tinha ferramenta que o
    servisse — e o modelo, em vez de dizer que não conseguia, inventava uma
    («update_timeline») e despejava a sintaxe da chamada em texto na conversa.
    """
    try:
        moment_id = int(id)
    except (TypeError, ValueError):
        return {"status": "error", "error": f"Invalid id {id!r}."}

    if not db.moment_belongs_to(ctx.user_id, moment_id):
        return {
            "status": "error",
            "error": f"No timeline entry with id {moment_id}. Search the timeline first.",
        }

    novo_texto = (content or "").strip() or None
    novo_dia = None
    if date and date.strip():
        novo_dia = parse_day(date)
        if novo_dia is None:
            return {"status": "error", "error": f"Could not understand the day {date!r}."}

    if novo_texto is None and novo_dia is None:
        return {
            "status": "error",
            "error": "Nothing to change: give new content, a new day, or both.",
        }

    db.update_moment(ctx.user_id, moment_id, novo_texto, novo_dia)
    actualizado = db.get_moments_between(
        ctx.user_id, novo_dia, novo_dia
    ) if novo_dia else None
    logger.info("Acontecimento #%s corrigido pelo utilizador %s.", moment_id, ctx.user_id)

    linha = next(
        (m for m in (actualizado or db.list_moments(ctx.user_id, limit=200))
         if m["id"] == moment_id),
        None,
    )
    return {
        "status": "ok",
        "moment": {
            "id": moment_id,
            "content": linha["content"] if linha else novo_texto,
            "day": format_day(linha["happened_on"]) if linha else format_day(novo_dia),
        },
    }


def search_timeline(ctx: ToolContext, query: str = "") -> dict[str, Any]:
    """Consulta a linha do tempo por dia, por período, por palavra ou sem filtro."""
    query = (query or "").strip()

    if not query:
        moments = db.list_moments(ctx.user_id)
        return {
            "status": "ok",
            "matched_by": "recent",
            "count": len(moments),
            "moments": [_serialise_moment(m) for m in moments],
        }

    # 1) Períodos que se dizem de uma assentada e que o dateparser não cobre.
    hoje = datetime.now(settings.tzinfo).date()
    periodo = _periodo_nomeado(query, hoje)
    if periodo is not None:
        inicio, fim = periodo
        moments = db.get_moments_between(ctx.user_id, inicio, fim)
        return {
            "status": "ok",
            "matched_by": "period",
            "from": inicio.isoformat(),
            "to": fim.isoformat(),
            "count": len(moments),
            "moments": [_serialise_moment(m) for m in moments],
        }

    # 2) Um dia concreto.
    dia = parse_day(query)
    if dia is not None and _looks_like_date(query):
        moments = db.get_moments_between(ctx.user_id, dia, dia)
        return {
            "status": "ok",
            "matched_by": "day",
            "day": dia.isoformat(),
            "count": len(moments),
            "moments": [_serialise_moment(m) for m in moments],
        }

    # 3) Procura no texto.
    moments = db.search_moments_by_text(ctx.user_id, query)
    return {
        "status": "ok",
        "matched_by": "text",
        "count": len(moments),
        "moments": [_serialise_moment(m) for m in moments],
    }


# Períodos com nome próprio. O dateparser devolve um instante, não um
# intervalo, por isso «semana passada» tem de ser tratado aqui.
_PERIODOS: list[tuple[str, int]] = [
    (r"\b(semana passada|last week|esta semana|this week)\b", 7),
    (r"\b(últimos|ultimos|last)\s+(\d+)\s+(dias|days)\b", 0),  # nº lido do texto
    (r"\b(mês passado|mes passado|last month|este mês|este mes|this month)\b", 30),
    (r"\b(este ano|this year|ano passado|last year)\b", 365),
]


def _periodo_nomeado(query: str, hoje: date) -> Optional[tuple[date, date]]:
    """Converte «semana passada», «últimos 10 dias» e afins num intervalo."""
    lowered = query.lower()
    for padrao, dias in _PERIODOS:
        match = re.search(padrao, lowered)
        if not match:
            continue
        if dias == 0:  # «últimos N dias»
            try:
                dias = int(match.group(2))
            except (IndexError, ValueError):
                continue
            dias = max(1, min(dias, 3650))
        return hoje - timedelta(days=dias), hoje
    return None


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
def _cancel_event_reminders(user_id: int, event_id: int) -> None:
    """Cancela os jobs dos lembretes de um evento antes de este desaparecer."""
    for reminder in db.get_reminders_for_event(user_id, event_id):
        scheduler.cancel_reminder(reminder["id"])


def delete_item(ctx: ToolContext, kind: str, id: int) -> dict[str, Any]:  # noqa: A002
    """Apaga um evento, uma nota ou um lembrete pelo respectivo id.

    A posse é confirmada **antes** de se tocar no scheduler. Pela ordem
    contrária, adivinhar um número inteiro bastava para calar os lembretes de
    outra pessoa: o apagar era recusado pela base de dados, mas o job já tinha
    sido cancelado — e o registo continuava a dizer «por disparar».
    """
    kind = (kind or "").strip().lower()
    try:
        item_id = int(id)
    except (TypeError, ValueError):
        return {"status": "error", "error": f"Invalid id {id!r}."}

    nao_existe = {"status": "error", "error": f"No {kind} with id {item_id}. Search for it first."}

    if kind == "event":
        if not db.event_belongs_to(ctx.user_id, item_id):
            return nao_existe
        # A base de dados apaga os lembretes em cascata, mas os jobs já
        # agendados têm de ser retirados do scheduler à mão.
        _cancel_event_reminders(ctx.user_id, item_id)
        apagado = db.delete_event(ctx.user_id, item_id)
    elif kind == "note":
        apagado = db.delete_note(ctx.user_id, item_id)
    elif kind == "moment":
        apagado = db.delete_moment(ctx.user_id, item_id)
    elif kind == "reminder":
        if not db.reminder_belongs_to(ctx.user_id, item_id):
            return nao_existe
        scheduler.cancel_reminder(item_id)
        apagado = db.delete_reminder(ctx.user_id, item_id)
    else:
        return {"status": "error", "error": "kind must be event, note, moment or reminder."}

    if not apagado:
        return nao_existe

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
    for reminder in db.get_reminders_for_event(ctx.user_id, event_id):
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

    try:
        db.set_preference(ctx.user_id, key, value)
    except db.TooManyPreferences:
        return {
            "status": "error",
            "error": (
                f"Preference limit reached ({settings.max_preferences}). "
                "Ask the user which existing one to drop, then remove it with an empty value."
            ),
        }
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
                "alert 15 min before. Use whenever the thing itself happens at a time, "
                "even if they say 'remind me' — it belongs in the diary."
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
                "Schedule a one-off alert for a task with no appointment behind it, "
                "e.g. 'remind me to take the pill at 9'. If something is actually "
                "happening at that time, use add_event instead."
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
                    "kind": {"type": "string", "enum": ["event", "note", "moment", "reminder"]},
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
            "name": "log_moment",
            "description": (
                "Record something that already happened, filed under the day it happened "
                "('we went to the aquarium', 'Bia went to the dentist'). No alert is sent. "
                "Use for anything in the past tense; add_event is for what is still to come."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "What happened."},
                    "date": {
                        "type": "string",
                        "description": "Day it happened, e.g. 'yesterday', 'last Thursday'. Empty means today.",
                    },
                },
                "required": ["content", "date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_moment",
            "description": (
                "Fix the wording or the day of a timeline entry already recorded. "
                "Search the timeline first to get its id. Use whenever they correct, "
                "rephrase or translate something you logged."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "Id from a timeline search."},
                    "content": {"type": "string", "description": "New wording. Omit to keep."},
                    "date": {"type": "string", "description": "New day. Omit to keep."},
                },
                "required": ["id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_timeline",
            "description": (
                "Look up what happened. Query: a day ('yesterday'), a period "
                "('last week', 'last 10 days'), a keyword, or empty for the most recent."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Day, period or keyword. May be empty."}
                },
                "required": ["query"],
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
    "log_moment": log_moment,
    "update_moment": update_moment,
    "search_timeline": search_timeline,
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
