"""Ligação ao modelo DeepSeek, gestão de memória e ciclo de *tool calling*.

A API DeepSeek é compatível com a da OpenAI, pelo que usamos o cliente oficial
`openai` apontado para `https://api.deepseek.com`.

Duas camadas de memória:

* **Curto prazo** — as últimas `MAX_HISTORY_MESSAGES` mensagens de cada
  utilizador, em RAM (dicionário indexado por `user_id`).
* **Longo prazo** — quando o histórico cresce demais, as mensagens mais antigas
  são condensadas pelo próprio modelo num resumo guardado na tabela
  `summaries`. Esse resumo é injectado no prompt de sistema de cada turno.

O cliente `openai` é síncrono; os handlers do Telegram chamam este módulo com
`asyncio.to_thread` para não bloquearem o event loop.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
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
_histories_lock = threading.Lock()


def get_history(user_id: int) -> list[dict[str, Any]]:
    """Cópia do histórico em memória de um utilizador."""
    with _histories_lock:
        return list(_histories.get(user_id, []))


def append_history(user_id: int, role: str, content: str) -> None:
    with _histories_lock:
        _histories.setdefault(user_id, []).append({"role": role, "content": content})


def reset_history(user_id: int) -> None:
    """Limpa a memória de curto prazo (o resumo em base de dados mantém-se)."""
    with _histories_lock:
        _histories.pop(user_id, None)


# ---------------------------------------------------------------------------
# Prompt de sistema
# ---------------------------------------------------------------------------
_PERSONA = """És o assistente pessoal de {nome}, a funcionar no Telegram.

REGRAS DE LÍNGUA
- Responde SEMPRE em português europeu (pt-PT). Nunca uses português do Brasil.
- Usa vocabulário e ortografia de Portugal: "telemóvel", "casa de banho", \
"comboio", "pequeno-almoço", "ecrã", "ficheiro", "autocarro".
- Trata o utilizador por tu, de forma natural e calorosa.

PERSONALIDADE
- Simpático, próximo e proactivo, mas conciso. Nada de respostas longas sem necessidade.
- Se detectares algo digno de ser guardado (uma data, um compromisso, uma ideia),
  sugere criar um evento, um lembrete ou uma nota — sem insistir.
- Conversa normalmente sobre qualquer assunto; não és apenas um executor de comandos.
- Se um pedido for ambíguo (falta a hora, falta o dia, falta o assunto), faz UMA
  pergunta curta de esclarecimento em vez de adivinhar.

FERRAMENTAS
- Antes de raciocinares sobre "hoje", "amanhã" ou "esta semana", chama
  `get_current_datetime`.
- Quando o utilizador marcar um compromisso, chama `add_event`.
- Quando pedir um aviso sem compromisso associado, chama `set_reminder`.
- Quando perguntar o que tem hoje/amanhã/numa data, chama `search_events`.
- Guarda informação útil com `save_note` e recupera-a com `search_notes`.
- Nunca inventes eventos, notas ou lembretes: se não vierem de uma ferramenta,
  não existem.

CONFIRMAÇÕES
- Depois de guardares um evento, um lembrete ou uma nota, confirma sempre
  mostrando exactamente o que ficou registado (descrição + data/hora por extenso)
  e, no caso dos eventos, a hora a que o aviso será enviado.
- Se uma ferramenta devolver estado "erro", explica o problema em linguagem
  simples e pergunta o que falta.

FORMATO
- Texto curto, com emojis pontuais (🗓️ ⏰ 📝 ✅) e listas quando ajudarem.
- Podes usar *negrito* e _itálico_ simples do Telegram, sem exageros."""


def build_system_prompt(ctx: ToolContext) -> str:
    """Constrói o prompt de sistema do turno, com data, memória e agenda do dia."""
    now = datetime.now(settings.tzinfo)
    nome = ctx.first_name or "o utilizador"

    partes = [
        _PERSONA.format(nome=nome),
        f"\n--- CONTEXTO ACTUAL ---\nData e hora: {tools.format_datetime(now)} "
        f"({settings.timezone}).",
        tools.get_daily_context(ctx.user_id),
    ]

    resumo = db.get_latest_summary(ctx.user_id)
    if resumo:
        partes.append(
            "\n--- MEMÓRIA DE CONVERSAS ANTERIORES ---\n"
            f"{resumo}\n"
            "(Usa esta memória com naturalidade; não a cites literalmente.)"
        )

    preferencias = db.get_preferences(ctx.user_id)
    if preferencias:
        linhas = "\n".join(f"- {chave}: {valor}" for chave, valor in preferencias.items())
        partes.append(f"\n--- PREFERÊNCIAS ---\n{linhas}")

    return "\n".join(partes)


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
    messages.append({"role": "user", "content": user_message})

    reply_text = ""

    for iteration in range(settings.max_tool_iterations):
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
                "Utilizador %s → ferramenta %s(%s)",
                ctx.user_id,
                call.function.name,
                arguments,
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
        reply_text = "Desculpa, não consegui formular uma resposta. Podes repetir de outra forma?"

    # Memória de curto prazo + eventual compactação.
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
        return get_client().chat.completions.create(**kwargs)
    except AuthenticationError as exc:
        logger.error("Chave DeepSeek inválida: %s", exc)
        raise AssistantError(
            "A minha chave de acesso ao modelo foi recusada. "
            "Verifica a variável DEEPSEEK_API_KEY."
        ) from exc
    except RateLimitError as exc:
        logger.warning("Limite de pedidos DeepSeek atingido: %s", exc)
        raise AssistantError(
            "Estou a receber pedidos a mais (ou o saldo da conta DeepSeek esgotou). "
            "Tenta novamente daqui a pouco."
        ) from exc
    except (APIConnectionError, APITimeoutError) as exc:
        logger.warning("Falha de ligação à DeepSeek: %s", exc)
        raise AssistantError(
            "Não consegui falar com o modelo — parece haver um problema de rede. "
            "Tenta outra vez dentro de instantes."
        ) from exc
    except APIStatusError as exc:
        logger.error("Erro da API DeepSeek (%s): %s", exc.status_code, exc)
        raise AssistantError(
            "O serviço do modelo devolveu um erro. Já registei o problema; tenta mais tarde."
        ) from exc
    except Exception as exc:  # noqa: BLE001 — o bot nunca deve morrer por causa da API
        logger.exception("Erro inesperado ao contactar a DeepSeek.")
        raise AssistantError(
            "Ocorreu um erro inesperado ao processar a tua mensagem. Tenta novamente."
        ) from exc


# ---------------------------------------------------------------------------
# Memória de longo prazo (summarize_memory — uso interno)
# ---------------------------------------------------------------------------
_SUMMARY_PROMPT = """Resume a conversa seguinte num parágrafo compacto em português europeu.

Guarda apenas o que é útil recordar a longo prazo:
- factos pessoais (nome, família, trabalho, saúde, gostos, rotinas);
- objectivos, projectos e decisões em curso;
- preferências sobre como o assistente deve comportar-se.

Ignora conversa de circunstância e detalhes irrelevantes. Escreve na terceira
pessoa ("O utilizador..."), no máximo 150 palavras, sem introduções."""


def summarize_memory(user_id: int, messages: list[dict[str, Any]], previous: Optional[str]) -> Optional[str]:
    """Condensa mensagens antigas num resumo, fundindo-o com o anterior.

    Ferramenta interna: não é exposta ao modelo no esquema de tools.
    """
    conversa = "\n".join(
        f"{'Utilizador' if entry['role'] == 'user' else 'Assistente'}: {entry['content']}"
        for entry in messages
        if entry.get("content")
    )
    if not conversa.strip():
        return None

    contexto = f"RESUMO ANTERIOR:\n{previous}\n\n" if previous else ""
    try:
        response = get_client().chat.completions.create(
            model=settings.deepseek_model,
            messages=[
                {"role": "system", "content": _SUMMARY_PROMPT},
                {"role": "user", "content": f"{contexto}CONVERSA A RESUMIR:\n{conversa}"},
            ],
            temperature=0.2,
            max_tokens=400,
        )
    except Exception:  # noqa: BLE001 — falhar a resumir nunca pode partir a conversa
        logger.exception("Falha ao gerar o resumo de memória do utilizador %s.", user_id)
        return None

    resumo = (response.choices[0].message.content or "").strip()
    return resumo or None


def _maybe_summarise(user_id: int) -> None:
    """Compacta o histórico quando ultrapassa o limite configurado."""
    with _histories_lock:
        history = _histories.get(user_id, [])
        if len(history) <= settings.max_history_messages:
            return
        cutoff = len(history) - settings.history_keep_messages
        older, recent = history[:cutoff], history[cutoff:]
        _histories[user_id] = recent

    logger.info("A resumir %d mensagens antigas do utilizador %s.", len(older), user_id)
    resumo = summarize_memory(user_id, older, db.get_latest_summary(user_id))
    if resumo:
        db.save_summary(user_id, resumo)
        db.prune_summaries(user_id)
        logger.info("Resumo de memória actualizado para o utilizador %s.", user_id)
