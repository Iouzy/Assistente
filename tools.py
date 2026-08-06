"""Ferramentas que o modelo DeepSeek pode invocar (function calling).

Cada ferramenta é uma função Python normal que devolve um dicionário
serializável em JSON. O esquema em `TOOL_SCHEMAS` segue exactamente o formato
de *tools* da OpenAI, que a API DeepSeek reproduz.

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
# Datas em português
# ---------------------------------------------------------------------------
_WEEKDAYS_PT = [
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
]

_MONTHS_PT = [
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
]

# Expressões frequentes que o dateparser não interpreta bem em pt-PT.
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


# Expressões relativas ("daqui a 20 minutos") são calculadas directamente: o
# dateparser não as cobre bem em pt-PT e a normalização acima transformaria
# "2 horas" em "2:00", alterando o sentido da frase.
_NUMBER_WORDS: dict[str, float] = {
    "um": 1, "uma": 1, "dois": 2, "duas": 2, "três": 3, "tres": 3,
    "quatro": 4, "cinco": 5, "seis": 6, "sete": 7, "oito": 8, "nove": 9,
    "dez": 10, "onze": 11, "doze": 12, "quinze": 15, "vinte": 20,
    "trinta": 30, "meia": 0.5, "meio": 0.5,
}

# Unidades ordenadas da mais longa para a mais curta (a alternância do `re`
# devolve a primeira que encaixar).
_UNIT_SECONDS: list[tuple[str, float]] = [
    ("segundos", 1), ("segundo", 1), ("seg", 1),
    ("minutos", 60), ("minuto", 60), ("mins", 60), ("min", 60),
    ("semanas", 604800), ("semana", 604800),
    ("horas", 3600), ("hora", 3600),
    ("meses", 2592000), ("mês", 2592000), ("mes", 2592000),
    ("dias", 86400), ("dia", 86400),
    ("anos", 31536000), ("ano", 31536000),
    ("h", 3600), ("m", 60), ("s", 1),
]

_RELATIVE_RE = re.compile(
    r"\b(?:daqui\s+a|d'?aqui\s+a|dentro\s+de|passad[oa]s?|em)\s+"
    r"(?P<quantia>\d+(?:[.,]\d+)?|"
    + "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True))
    + r")\s*"
    r"(?P<unidade>" + "|".join(unit for unit, _ in _UNIT_SECONDS) + r")\b"
)


def _parse_relative(text: str, now: datetime) -> Optional[datetime]:
    """Interpreta expressões do tipo «daqui a 20 minutos» / «dentro de 2 horas»."""
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

    unidade = match.group("unidade")
    segundos = next(valor for nome, valor in _UNIT_SECONDS if nome == unidade)
    return now + timedelta(seconds=quantia * segundos)


def _normalise_date_text(text: str) -> str:
    """Limpa e uniformiza expressões temporais em português antes da análise."""
    cleaned = text.strip().lower()
    for pattern, replacement in _NORMALISATIONS:
        cleaned = re.sub(pattern, replacement, cleaned)
    # "às"/"as" antes da hora só confunde o parser.
    cleaned = re.sub(r"\b[àa]s?\b", " ", cleaned)
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

    # Tenta primeiro o texto normalizado; se falhar, o original.
    for candidate in (_normalise_date_text(text), text.strip()):
        if not candidate:
            continue
        parsed = dateparser.parse(candidate, languages=["pt", "en"], settings=parser_settings)
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=settings.tzinfo)
            return parsed.astimezone(settings.tzinfo)
    return None


def format_datetime(value: datetime | str) -> str:
    """Formata uma data/hora para leitura humana em pt-PT."""
    dt = _coerce_datetime(value)
    if dt is None:
        return str(value)
    return (
        f"{_WEEKDAYS_PT[dt.weekday()]}, {dt.day} de {_MONTHS_PT[dt.month - 1]} "
        f"de {dt.year} às {dt:%H:%M}"
    )


def format_short(value: datetime | str) -> str:
    """Formato compacto: `07/08/2026 15:00`."""
    dt = _coerce_datetime(value)
    return dt.strftime("%d/%m/%Y %H:%M") if dt else str(value)


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
        "descricao": event["description"],
        "data_iso": event["event_time"],
        "data_legivel": format_datetime(event["event_time"]),
    }


def _serialise_note(note: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": note["id"],
        "conteudo": note["content"],
        "criada_em": format_short(note["created_at"]),
    }


# ---------------------------------------------------------------------------
# Ferramenta 1 — data e hora actuais
# ---------------------------------------------------------------------------
def get_current_datetime(ctx: ToolContext) -> dict[str, Any]:
    """Devolve a data e a hora actuais no fuso configurado."""
    now = datetime.now(settings.tzinfo)
    return {
        "estado": "ok",
        "data_hora_iso": now.isoformat(),
        "data_hora_legivel": format_datetime(now),
        "dia_da_semana": _WEEKDAYS_PT[now.weekday()],
        "fuso_horario": settings.timezone,
    }


# ---------------------------------------------------------------------------
# Ferramenta 2 — criar evento (+ lembrete automático)
# ---------------------------------------------------------------------------
def add_event(ctx: ToolContext, date: str, description: str) -> dict[str, Any]:
    """Guarda um compromisso e agenda o lembrete prévio."""
    description = (description or "").strip()
    if not description:
        return {
            "estado": "erro",
            "erro": "Falta a descrição do evento. Pergunte ao utilizador o que é o compromisso.",
        }

    event_time = parse_datetime(date)
    if event_time is None:
        return {
            "estado": "erro",
            "erro": (
                f"Não consegui interpretar a data {date!r}. "
                "Peça ao utilizador uma data mais explícita (ex.: 'amanhã às 15:00' "
                "ou '12/09/2026 09:30')."
            ),
        }

    now = datetime.now(settings.tzinfo)
    event_id = db.create_event(ctx.user_id, ctx.chat_id, description, event_time)

    # Lembrete automático, por omissão 15 minutos antes.
    reminder_info: dict[str, Any] = {"criado": False}
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
            message += f"\n(faltam {settings.event_reminder_lead_minutes} minutos)"

        reminder_id = db.create_reminder(
            user_id=ctx.user_id,
            chat_id=ctx.chat_id,
            message=message,
            remind_at=remind_at,
            kind="event",
            event_id=event_id,
        )
        scheduler.schedule_reminder(reminder_id, remind_at)
        reminder_info = {
            "criado": True,
            "id": reminder_id,
            "hora_iso": remind_at.isoformat(),
            "hora_legivel": format_datetime(remind_at),
        }

    logger.info("Evento #%s criado para o utilizador %s.", event_id, ctx.user_id)
    return {
        "estado": "ok",
        "evento": {
            "id": event_id,
            "descricao": description,
            "data_iso": event_time.isoformat(),
            "data_legivel": format_datetime(event_time),
        },
        "lembrete": reminder_info,
        "no_passado": event_time <= now,
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
            "estado": "ok",
            "tipo_de_pesquisa": "proximos",
            "total": len(events),
            "eventos": [_serialise_event(event) for event in events],
        }

    # 1) Tentativa por data. `prefer_future=False` evita que "hoje" salte para
    #    amanhã e permite consultar dias passados.
    day = parse_datetime(query, prefer_future=False)
    if day is not None:
        start, end = _day_bounds(day)
        events = db.get_events_between(ctx.user_id, start, end)
        if events or _looks_like_date(query):
            return {
                "estado": "ok",
                "tipo_de_pesquisa": "data",
                "dia": format_short(start).split(" ")[0],
                "total": len(events),
                "eventos": [_serialise_event(event) for event in events],
            }

    # 2) Pesquisa textual.
    events = db.search_events_by_text(ctx.user_id, query)
    return {
        "estado": "ok",
        "tipo_de_pesquisa": "texto",
        "termo": query,
        "total": len(events),
        "eventos": [_serialise_event(event) for event in events],
    }


_DATE_HINTS = (
    "hoje", "amanhã", "amanha", "ontem", "depois de amanhã", "próxima", "proxima",
    "segunda", "terça", "terca", "quarta", "quinta", "sexta", "sábado", "sabado",
    "domingo", "semana", "mês", "mes", "janeiro", "fevereiro", "março", "marco",
    "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
    "novembro", "dezembro",
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
        return {"estado": "erro", "erro": "A nota está vazia. Pergunte o que deve ser guardado."}

    note = db.create_note(ctx.user_id, content)
    logger.info("Nota #%s criada para o utilizador %s.", note["id"], ctx.user_id)
    return {"estado": "ok", "nota": _serialise_note(note)}


# ---------------------------------------------------------------------------
# Ferramenta 5 — procurar notas
# ---------------------------------------------------------------------------
def search_notes(ctx: ToolContext, query: str = "") -> dict[str, Any]:
    """Procura notas por texto; sem termo devolve as mais recentes."""
    query = (query or "").strip()
    notes = db.list_notes(ctx.user_id) if not query else db.search_notes_by_text(ctx.user_id, query)
    return {
        "estado": "ok",
        "termo": query or "(mais recentes)",
        "total": len(notes),
        "notas": [_serialise_note(note) for note in notes],
    }


# ---------------------------------------------------------------------------
# Ferramenta 6 — lembrete simples
# ---------------------------------------------------------------------------
def set_reminder(ctx: ToolContext, message: str, time: str) -> dict[str, Any]:
    """Agenda um lembrete pontual para uma hora indicada."""
    message = (message or "").strip()
    if not message:
        return {"estado": "erro", "erro": "Falta o texto do lembrete."}

    remind_at = parse_datetime(time)
    if remind_at is None:
        return {
            "estado": "erro",
            "erro": (
                f"Não consegui interpretar a hora {time!r}. "
                "Peça algo como 'às 9:00', 'daqui a 20 minutos' ou 'amanhã às 8:30'."
            ),
        }

    now = datetime.now(settings.tzinfo)
    if remind_at <= now:
        # Horas soltas ("9:00") que já passaram referem-se ao dia seguinte.
        if now - remind_at < timedelta(days=1):
            remind_at += timedelta(days=1)
        else:
            return {
                "estado": "erro",
                "erro": (
                    f"A hora indicada ({format_datetime(remind_at)}) está no passado. "
                    "Peça uma hora futura."
                ),
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
        "estado": "ok",
        "lembrete": {
            "id": reminder_id,
            "mensagem": message,
            "hora_iso": remind_at.isoformat(),
            "hora_legivel": format_datetime(remind_at),
        },
    }


# ---------------------------------------------------------------------------
# Ferramenta 7 — lembretes pendentes (apoio à consulta "o que tenho agendado?")
# ---------------------------------------------------------------------------
def list_reminders(ctx: ToolContext) -> dict[str, Any]:
    """Lista os lembretes que ainda não dispararam."""
    reminders = db.get_user_reminders(ctx.user_id)
    return {
        "estado": "ok",
        "total": len(reminders),
        "lembretes": [
            {
                "id": reminder["id"],
                "mensagem": reminder["message"],
                "hora_legivel": format_datetime(reminder["remind_at"]),
                "tipo": "compromisso" if reminder["kind"] == "event" else "simples",
            }
            for reminder in reminders
        ],
    }


# ---------------------------------------------------------------------------
# Contexto de memória (usado na construção do prompt de sistema)
# ---------------------------------------------------------------------------
def get_daily_context(user_id: int) -> str:
    """Resumo curto do dia, injectado no prompt para respostas mais directas."""
    now = datetime.now(settings.tzinfo)
    start, end = _day_bounds(now)
    today = db.get_events_between(user_id, start, end)

    if not today:
        return "Não há compromissos registados para hoje."

    linhas = [f"- {format_short(event['event_time'])[11:]} — {event['description']}" for event in today]
    return "Compromissos de hoje:\n" + "\n".join(linhas)


# ---------------------------------------------------------------------------
# Esquemas no formato OpenAI / DeepSeek
# ---------------------------------------------------------------------------
TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_current_datetime",
            "description": (
                "Devolve a data e a hora actuais. Use sempre esta ferramenta antes de "
                "raciocinar sobre datas relativas (hoje, amanhã, esta semana)."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_event",
            "description": (
                "Guarda um compromisso na agenda do utilizador e agenda automaticamente "
                "um lembrete 15 minutos antes. Use quando o utilizador marca uma reunião, "
                "consulta, jantar, viagem ou qualquer evento com data."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": (
                            "Data e hora do evento em linguagem natural ou ISO. "
                            "Exemplos: 'amanhã às 15:00', 'sexta-feira às 9h30', "
                            "'12/09/2026 18:00'."
                        ),
                    },
                    "description": {
                        "type": "string",
                        "description": "Descrição curta do compromisso, em português.",
                    },
                },
                "required": ["date", "description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_events",
            "description": (
                "Procura compromissos na agenda. A consulta pode ser uma data "
                "('hoje', 'amanhã', '15/09'), uma palavra-chave ('dentista') ou ficar "
                "vazia para devolver os próximos compromissos."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Data ou palavra-chave a procurar. Pode ser uma string vazia.",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_note",
            "description": (
                "Guarda uma nota com data/hora: ideias, listas, dados a recordar, "
                "preferências pessoais ou qualquer informação sem data associada."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Conteúdo da nota."}
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_notes",
            "description": (
                "Procura nas notas guardadas. Consulta vazia devolve as notas mais recentes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Texto a procurar nas notas."}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_reminder",
            "description": (
                "Agenda um lembrete pontual que será enviado ao utilizador no Telegram. "
                "Use para avisos sem compromisso associado ('lembra-me de tomar o "
                "medicamento às 9:00')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Texto a enviar quando o lembrete disparar.",
                    },
                    "time": {
                        "type": "string",
                        "description": (
                            "Hora do lembrete em linguagem natural ou ISO. Exemplos: "
                            "'9:00', 'daqui a 30 minutos', 'amanhã às 8h'."
                        ),
                    },
                },
                "required": ["message", "time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_reminders",
            "description": "Lista os lembretes ainda por disparar do utilizador.",
            "parameters": {"type": "object", "properties": {}, "required": []},
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
}


def execute_tool(name: str, arguments: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """Executa uma ferramenta pelo nome, devolvendo sempre um dicionário.

    Erros nunca são propagados: viram um resultado de estado "erro" que o modelo
    consegue explicar ao utilizador.
    """
    function = _REGISTRY.get(name)
    if function is None:
        logger.warning("Ferramenta desconhecida pedida pelo modelo: %s", name)
        return {"estado": "erro", "erro": f"A ferramenta {name!r} não existe."}

    try:
        # Só os argumentos previstos são passados adiante — o modelo pode
        # inventar chaves extra e isso não deve rebentar a chamada.
        allowed = _allowed_arguments(name)
        kwargs = {key: value for key, value in (arguments or {}).items() if key in allowed}
        return function(ctx, **kwargs)
    except TypeError as exc:
        logger.warning("Argumentos inválidos para %s: %s", name, exc)
        return {"estado": "erro", "erro": f"Argumentos inválidos para {name}: {exc}"}
    except Exception as exc:  # noqa: BLE001 — resiliência é intencional aqui
        logger.exception("Erro ao executar a ferramenta %s.", name)
        return {"estado": "erro", "erro": f"Falha interna ao executar {name}: {exc}"}


def _allowed_arguments(name: str) -> set[str]:
    """Nomes de parâmetros declarados no esquema de uma ferramenta."""
    for schema in TOOL_SCHEMAS:
        if schema["function"]["name"] == name:
            return set(schema["function"]["parameters"].get("properties", {}))
    return set()
