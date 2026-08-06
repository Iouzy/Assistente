"""Ligação ao modelo DeepSeek, gestão de memória e ciclo de *tool calling*.

A API DeepSeek é compatível com a da OpenAI, pelo que usamos o cliente oficial
`openai` apontado para `https://api.deepseek.com`.

## Memória em duas camadas

* **Curto prazo** — as últimas `MAX_HISTORY_MESSAGES` mensagens de cada
  utilizador, em RAM.
* **Longo prazo** — quando o histórico cresce demais, as mensagens mais antigas
  são condensadas pelo modelo num resumo guardado na tabela `summaries`.

Uma conversa curta que nunca atinja o limite ficaria só em RAM e perder-se-ia
ao desligar o bot. Para evitar isso há dois momentos de arrumação:
`flush_idle()`, chamado periodicamente para conversas paradas há muito, e
`flush_all()`, chamado no encerramento.

## Ordem do prompt e cache

A DeepSeek desconta fortemente os prefixos repetidos entre chamadas (*context
caching*), mas só enquanto o início do pedido for **exactamente igual**. Por
isso tudo o que varia — data e hora, agenda do dia, resumo de memória — vai
junto da **última** mensagem, e nunca no prompt de sistema. Assim as partes
caras e estáveis (catálogo de ferramentas, persona, histórico anterior) formam
um prefixo constante que a API pode reaproveitar.

O cliente `openai` é síncrono; os handlers do Telegram chamam este módulo com
`asyncio.to_thread` para não bloquearem o event loop.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta
from typing import Any, Optional

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    OpenAI,
    RateLimitError,
)

import database as db
import tools
from config import settings
from tools import ToolContext

logger = logging.getLogger(__name__)


class AssistantError(RuntimeError):
    """Erro já traduzido para uma mensagem apresentável ao utilizador."""


# ---------------------------------------------------------------------------
# Cliente
# ---------------------------------------------------------------------------
_client: Optional[OpenAI] = None
_client_lock = threading.Lock()


def get_client() -> OpenAI:
    """Devolve o cliente DeepSeek (criado uma única vez)."""
    global _client
    with _client_lock:
        if _client is None:
            _client = OpenAI(
                api_key=settings.deepseek_api_key,
                base_url=settings.deepseek_base_url,
                timeout=60.0,
                max_retries=2,
            )
        return _client


# ---------------------------------------------------------------------------
# Memória de curto prazo
# ---------------------------------------------------------------------------
_histories: dict[int, list[dict[str, Any]]] = {}
_last_activity: dict[int, datetime] = {}
_histories_lock = threading.Lock()


def get_history(user_id: int) -> list[dict[str, Any]]:
    """Cópia do histórico em memória de um utilizador."""
    with _histories_lock:
        return list(_histories.get(user_id, []))


def append_history(user_id: int, role: str, content: str) -> None:
    with _histories_lock:
        _histories.setdefault(user_id, []).append({"role": role, "content": content})
        _last_activity[user_id] = datetime.now(settings.tzinfo)


def reset_history(user_id: int) -> None:
    """Limpa a memória de curto prazo (o resumo em base de dados mantém-se)."""
    with _histories_lock:
        _histories.pop(user_id, None)
        _last_activity.pop(user_id, None)


# ---------------------------------------------------------------------------
# Prompt: parte estável (sistema) e parte volátil (junto à última mensagem)
# ---------------------------------------------------------------------------
_PERSONA = """You are {name}'s personal assistant on Telegram.

STYLE
- Reply in English. Warm, direct, concise — no filler, no restating the question.
- Chat normally about anything; you are not just a command runner.
- When something is worth keeping, offer to save it as a note, event or reminder — once, without nagging.
- If a request is missing a date, a time or a subject, ask ONE short question instead of guessing.

TOOLS
- Call get_current_datetime before reasoning about today, tomorrow or this week.
- Never invent events, notes or reminders. If a tool did not return it, it does not exist.
- To change or delete something, search for it first — you need its id. If several match, ask which.
- When they state a lasting preference (what to call them, tone, emoji), save it with set_preference.

CONFIRMATIONS
- After saving anything, confirm exactly what was stored: description plus the full date and time,
  and for events the time the alert will be sent.
- If a tool returns status "error", explain it plainly and ask for what is missing.

FORMAT
- Short text, the odd emoji (🗓️ ⏰ 📝 ✅), lists when they help. Light *bold* and _italic_ only.

A [context] line may precede the user's message with the current time, today's diary and what you
remember about them. It is background, not something they typed — never quote it back."""


def build_system_prompt(ctx: ToolContext) -> str:
    """Parte estável do prompt. Não pode conter nada que mude a cada turno."""
    return _PERSONA.format(name=ctx.first_name or "the user")


def build_context_block(user_id: int) -> str:
    """Parte volátil: data/hora, agenda do dia e memória de longo prazo.

    Vai colada à última mensagem do utilizador, e não ao prompt de sistema,
    para não invalidar a cache de prefixo da DeepSeek.
    """
    now = datetime.now(settings.tzinfo)
    linhas = [f"[context: now {tools.format_datetime(now)}. {tools.get_daily_context(user_id)}]"]

    resumo = db.get_latest_summary(user_id)
    if resumo:
        linhas.append(f"[memory: {resumo}]")

    preferencias = db.get_preferences(user_id)
    if preferencias:
        pares = ", ".join(f"{chave}={valor}" for chave, valor in preferencias.items())
        linhas.append(f"[preferences: {pares}]")

    return "\n".join(linhas)


# ---------------------------------------------------------------------------
# Ciclo principal de conversa
# ---------------------------------------------------------------------------
def generate_reply(ctx: ToolContext, user_message: str) -> str:
    """Processa uma mensagem do utilizador e devolve a resposta do assistente.

    Executa o ciclo completo de *tool calling*: pede uma resposta ao modelo,
    executa as ferramentas que ele pedir, devolve-lhe os resultados e repete até
    haver uma resposta em texto.
    """
    messages: list[dict[str, Any]] = [{"role": "system", "content": build_system_prompt(ctx)}]
    messages.extend(get_history(ctx.user_id))
    messages.append(
        {"role": "user", "content": f"{build_context_block(ctx.user_id)}\n\n{user_message}"}
    )

    reply_text = ""

    for _ in range(settings.max_tool_iterations):
        response = _chat_completion(messages, with_tools=True)
        message = response.choices[0].message

        if not message.tool_calls:
            reply_text = (message.content or "").strip()
            break

        # O modelo pediu ferramentas: registamos o pedido...
        messages.append(
            {
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.function.name,
                            "arguments": call.function.arguments or "{}",
                        },
                    }
                    for call in message.tool_calls
                ],
            }
        )

        # ...executamos cada uma e devolvemos os resultados ao modelo.
        for call in message.tool_calls:
            arguments = _parse_arguments(call.function.arguments)
            logger.info(
                "Utilizador %s → ferramenta %s(%s)", ctx.user_id, call.function.name, arguments
            )
            result = tools.execute_tool(call.function.name, arguments, ctx)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )
    else:
        # Esgotou as rondas de ferramentas: força uma resposta final em texto.
        logger.warning("Limite de rondas de ferramentas atingido (utilizador %s).", ctx.user_id)
        response = _chat_completion(messages, with_tools=False)
        reply_text = (response.choices[0].message.content or "").strip()

    if not reply_text:
        reply_text = "Sorry, I could not put an answer together. Could you rephrase that?"

    # Guardamos a mensagem *sem* o bloco de contexto: o histórico tem de ser
    # apenas acrescentado, nunca reescrito, ou a cache de prefixo deixa de bater.
    append_history(ctx.user_id, "user", user_message)
    append_history(ctx.user_id, "assistant", reply_text)
    _maybe_summarise(ctx.user_id)

    return reply_text


def _parse_arguments(raw: Optional[str]) -> dict[str, Any]:
    """Converte os argumentos JSON de uma tool call, tolerando lixo."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        logger.warning("Argumentos de ferramenta não são JSON válido: %r", raw)
        return {}


def _chat_completion(messages: list[dict[str, Any]], with_tools: bool):
    """Chama a API DeepSeek, traduzindo falhas em `AssistantError`."""
    kwargs: dict[str, Any] = {
        "model": settings.deepseek_model,
        "messages": messages,
        "temperature": 0.5,
        "max_tokens": 1200,
    }
    if with_tools:
        kwargs["tools"] = tools.TOOL_SCHEMAS
        kwargs["tool_choice"] = "auto"

    try:
        response = get_client().chat.completions.create(**kwargs)
    except AuthenticationError as exc:
        logger.error("Chave DeepSeek inválida: %s", exc)
        raise AssistantError(
            "My model credentials were rejected. Check the DEEPSEEK_API_KEY setting."
        ) from exc
    except RateLimitError as exc:
        logger.warning("Limite de pedidos DeepSeek atingido: %s", exc)
        raise AssistantError(
            "I am getting rate limited (or the DeepSeek account is out of credit). "
            "Try again shortly."
        ) from exc
    except (APIConnectionError, APITimeoutError) as exc:
        logger.warning("Falha de ligação à DeepSeek: %s", exc)
        raise AssistantError(
            "I could not reach the model — looks like a network problem. Try again in a moment."
        ) from exc
    except APIStatusError as exc:
        logger.error("Erro da API DeepSeek (%s): %s", exc.status_code, exc)
        raise AssistantError(
            "The model service returned an error. It is logged; please try again later."
        ) from exc
    except Exception as exc:  # noqa: BLE001 — o bot nunca deve morrer por causa da API
        logger.exception("Erro inesperado ao contactar a DeepSeek.")
        raise AssistantError("Something unexpected went wrong. Please try again.") from exc

    _log_usage(response)
    return response


def _log_usage(response) -> None:
    """Regista os tokens gastos e quantos foram servidos pela cache."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    # A DeepSeek devolve estes dois campos extra; outros fornecedores não.
    hit = getattr(usage, "prompt_cache_hit_tokens", None)
    miss = getattr(usage, "prompt_cache_miss_tokens", None)
    if hit is None:
        logger.debug("Tokens: %s entrada, %s saída", usage.prompt_tokens, usage.completion_tokens)
    else:
        total = (hit or 0) + (miss or 0)
        percentagem = (hit / total * 100) if total else 0.0
        logger.debug(
            "Tokens: %s entrada (%s em cache, %.0f%%), %s saída",
            usage.prompt_tokens,
            hit,
            percentagem,
            usage.completion_tokens,
        )


# ---------------------------------------------------------------------------
# Memória de longo prazo (summarize_memory — uso interno)
# ---------------------------------------------------------------------------
_SUMMARY_PROMPT = """Summarise the conversation below in one compact paragraph.

Keep only what is worth remembering long term:
- personal facts (name, family, work, health, tastes, routines);
- ongoing goals, projects and decisions;
- preferences about how the assistant should behave.

Drop small talk and one-off details. Write in the third person ("The user..."),
150 words maximum, no preamble."""


def summarize_memory(
    user_id: int, messages: list[dict[str, Any]], previous: Optional[str]
) -> Optional[str]:
    """Condensa mensagens antigas num resumo, fundindo-o com o anterior.

    Ferramenta interna: não é exposta ao modelo no esquema de tools.
    """
    conversa = "\n".join(
        f"{'User' if entry['role'] == 'user' else 'Assistant'}: {entry['content']}"
        for entry in messages
        if entry.get("content")
    )
    if not conversa.strip():
        return None

    contexto = f"PREVIOUS SUMMARY:\n{previous}\n\n" if previous else ""
    try:
        response = get_client().chat.completions.create(
            model=settings.deepseek_model,
            messages=[
                {"role": "system", "content": _SUMMARY_PROMPT},
                {"role": "user", "content": f"{contexto}CONVERSATION:\n{conversa}"},
            ],
            temperature=0.2,
            max_tokens=400,
        )
    except Exception:  # noqa: BLE001 — falhar a resumir nunca pode partir a conversa
        logger.exception("Falha ao gerar o resumo de memória do utilizador %s.", user_id)
        return None

    resumo = (response.choices[0].message.content or "").strip()
    return resumo or None


def _summarise_and_drop(user_id: int, count: int) -> bool:
    """Resume as `count` mensagens mais antigas e remove-as do histórico.

    A chamada à API é feita **fora** do lock: seria uma chamada de rede a
    bloquear todas as outras conversas. Por isso tiramos uma fotografia das
    mensagens, resumimos, e só depois voltamos ao lock para remover exactamente
    as que resumimos — mensagens que tenham chegado entretanto ficam intactas.
    """
    with _histories_lock:
        history = _histories.get(user_id, [])
        if count <= 0 or not history:
            return False
        older = history[:count]

    resumo = summarize_memory(user_id, older, db.get_latest_summary(user_id))
    if not resumo:
        return False

    db.save_summary(user_id, resumo)
    db.prune_summaries(user_id)

    with _histories_lock:
        history = _histories.get(user_id, [])
        _histories[user_id] = history[len(older):]

    logger.info("Memória do utilizador %s actualizada (%d mensagens arrumadas).", user_id, len(older))
    return True


def _maybe_summarise(user_id: int) -> None:
    """Compacta o histórico quando ultrapassa o limite configurado."""
    with _histories_lock:
        total = len(_histories.get(user_id, []))
    if total <= settings.max_history_messages:
        return
    _summarise_and_drop(user_id, total - settings.history_keep_messages)


# ---------------------------------------------------------------------------
# Arrumação: conversas paradas e encerramento
# ---------------------------------------------------------------------------
def flush_user(user_id: int) -> bool:
    """Resume tudo o que está em memória para este utilizador e esvazia-a.

    Usado quando a conversa acabou (silêncio prolongado ou encerramento do
    bot): sem isto, uma conversa que nunca atingiu o limite desaparecia sem
    deixar rasto na base de dados.
    """
    with _histories_lock:
        total = len(_histories.get(user_id, []))
    if total == 0:
        return False
    return _summarise_and_drop(user_id, total)


def flush_idle() -> int:
    """Arruma as conversas sem actividade há mais de `IDLE_FLUSH_MINUTES`."""
    if settings.idle_flush_minutes <= 0:
        return 0

    limite = datetime.now(settings.tzinfo) - timedelta(minutes=settings.idle_flush_minutes)
    with _histories_lock:
        parados = [
            user_id
            for user_id, quando in _last_activity.items()
            if quando < limite and _histories.get(user_id)
        ]

    arrumados = 0
    for user_id in parados:
        try:
            if flush_user(user_id):
                arrumados += 1
        except Exception:  # noqa: BLE001 — corre numa thread do scheduler
            logger.exception("Falha ao arrumar a conversa do utilizador %s.", user_id)

    if arrumados:
        logger.info("%d conversa(s) parada(s) arrumada(s) na memória de longo prazo.", arrumados)
    return arrumados


def flush_all() -> int:
    """Arruma todas as conversas em memória. Chamado no encerramento."""
    with _histories_lock:
        pendentes = [user_id for user_id, history in _histories.items() if history]

    if not pendentes:
        return 0

    logger.info("A guardar %d conversa(s) em memória antes de encerrar...", len(pendentes))
    guardados = 0
    for user_id in pendentes:
        try:
            if flush_user(user_id):
                guardados += 1
        except Exception:  # noqa: BLE001 — encerrar nunca pode falhar por isto
            logger.exception("Falha ao guardar a conversa do utilizador %s.", user_id)
    return guardados
