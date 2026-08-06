"""Verifica a que ferramenta o modelo atribui cada tipo de mensagem.

Ao contrário dos outros testes, este **fala com a API DeepSeek a sério** — é a
única forma de saber se as descrições das ferramentas são claras o suficiente.
Custa poucos cêntimos (cerca de 30 chamadas curtas).

É um ensaio a seco: as ferramentas nunca chegam a ser executadas. Quando o
modelo pede uma consulta (ver as horas, procurar na agenda), devolvemos um
resultado inventado e deixamo-lo continuar; a ferramenta que ele escolher a
seguir é a que fica registada. Nada é escrito na base de dados real.

Uso:
    python tests/test_tool_choice.py
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile

# Base de dados temporária: os dados reais não são tocados.
os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "ensaio.db")
os.environ.setdefault("TELEGRAM_TOKEN", "123:FAKE")
os.environ["LOG_LEVEL"] = "ERROR"

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import logging  # noqa: E402

logging.basicConfig(level=logging.ERROR)

import database as db  # noqa: E402
import llm  # noqa: E402
import tools  # noqa: E402
from config import ConfigError, settings  # noqa: E402
from tools import ToolContext  # noqa: E402

# ---------------------------------------------------------------------------
# Casos de teste: (mensagem, ferramenta esperada)
#
# A ferramenta esperada pode ser:
#   * um nome            -> tem de ser exactamente essa
#   * um conjunto        -> qualquer uma serve
#   * None               -> não deve usar ferramenta nenhuma (conversa normal)
#   * "?"                -> genuinamente ambíguo: só queremos ver o que escolhe
# ---------------------------------------------------------------------------
CASOS: list[tuple[str, object]] = [
    # --- criar compromissos ---
    ("dentist tomorrow at 3pm", "add_event"),
    ("lunch with Ana on Friday at 1pm", "add_event"),
    ("I have a doctor's appointment on the 12th at 9:30", "add_event"),
    ("marca dentista amanhã às 15h", "add_event"),  # português ainda funciona?

    # --- alertas soltos ---
    ("remind me to take the pill at 9", "set_reminder"),
    ("remind me to call the garage in 20 minutes", "set_reminder"),
    ("ping me at 6pm to take the bins out", "set_reminder"),

    # --- a fronteira delicada: evento ou lembrete? ---
    ("remind me about the meeting tomorrow at 10", "?"),
    ("don't let me forget the dentist on Friday", "?"),

    # --- consultar a agenda ---
    ("what's on today?", "search_events"),
    ("what do I have on Friday?", "search_events"),
    ("when is the dentist?", "search_events"),
    ("what's coming up?", "search_events"),

    # --- notas ---
    ("note: the alarm code is 4471", "save_note"),
    ("remember the office wifi is Torre2024", "save_note"),
    ("what was the alarm code?", "search_notes"),

    # --- apagar e alterar (precisam de procurar primeiro) ---
    ("cancel the dentist", "delete_item"),
    ("delete that note about the wifi", "delete_item"),
    ("push the dentist to 4pm", "update_event"),
    ("rename the dentist appointment to Dr Silva", "update_event"),

    # --- preferências ---
    ("call me Mike from now on", "set_preference"),
    ("stop using emojis please", "set_preference"),

    # --- alertas pendentes ---
    ("what alerts do I have?", {"list_reminders", "search_events"}),

    # --- conversa normal: NÃO deve chamar ferramentas ---
    ("hi, how are you?", None),
    ("I'm a bit stressed about work", None),
    ("thanks, that's great", None),
]

# Ferramentas de consulta: quando o modelo as pede, respondemos e deixamo-lo
# continuar, porque o que interessa é a acção que ele toma a seguir.
CONSULTAS = {"get_current_datetime", "search_events", "search_notes", "list_reminders"}

RESPOSTAS_INVENTADAS = {
    "get_current_datetime": lambda ctx: tools.get_current_datetime(ctx),
    "search_events": lambda ctx: {
        "status": "ok",
        "matched_by": "text",
        "count": 1,
        "events": [{"id": 42, "description": "Dentist", "when": "Friday, 7 August 2026 at 15:00"}],
    },
    "search_notes": lambda ctx: {
        "status": "ok",
        "count": 1,
        "notes": [{"id": 7, "content": "office wifi is Torre2024", "saved": "06/08/2026 11:20"}],
    },
    "list_reminders": lambda ctx: {
        "status": "ok",
        "count": 1,
        "reminders": [{"id": 5, "message": "take the pill", "at": "Friday, 7 August 2026 at 09:00"}],
    },
}


def escolha_do_modelo(ctx: ToolContext, mensagem: str) -> tuple[list[str], dict, dict]:
    """Devolve (ferramentas pedidas, argumentos da última, contagem de tokens)."""
    messages: list[dict] = [{"role": "system", "content": llm.build_system_prompt(ctx)}]
    messages.append(
        {"role": "user", "content": f"{llm.build_context_block(ctx.user_id)}\n\n{mensagem}"}
    )

    pedidas: list[str] = []
    argumentos: dict = {}
    tokens = {"entrada": 0, "cache": 0, "saida": 0}

    for _ in range(4):
        resposta = llm.get_client().chat.completions.create(
            model=settings.deepseek_model,
            messages=messages,
            tools=tools.TOOL_SCHEMAS,
            tool_choice="auto",
            temperature=0.0,  # determinista, para o teste ser repetível
            max_tokens=300,
        )

        uso = getattr(resposta, "usage", None)
        if uso:
            tokens["entrada"] += uso.prompt_tokens
            tokens["saida"] += uso.completion_tokens
            tokens["cache"] += getattr(uso, "prompt_cache_hit_tokens", 0) or 0

        message = resposta.choices[0].message
        if not message.tool_calls:
            break

        chamada = message.tool_calls[0]
        pedidas.append(chamada.function.name)
        try:
            argumentos = json.loads(chamada.function.arguments or "{}")
        except json.JSONDecodeError:
            argumentos = {"<json inválido>": chamada.function.arguments}

        # Se não é uma consulta, é a acção final: paramos aqui.
        if chamada.function.name not in CONSULTAS:
            break

        messages.append(
            {
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {"name": c.function.name, "arguments": c.function.arguments or "{}"},
                    }
                    for c in message.tool_calls
                ],
            }
        )
        for c in message.tool_calls:
            inventar = RESPOSTAS_INVENTADAS.get(c.function.name)
            resultado = inventar(ctx) if inventar else {"status": "ok"}
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": c.id,
                    "content": json.dumps(resultado, ensure_ascii=False),
                }
            )

    return pedidas, argumentos, tokens


def avalia(esperado: object, pedidas: list[str]) -> tuple[str, str]:
    """Devolve (símbolo, comentário) para um caso."""
    # A última ferramenta que não é uma mera consulta é a decisão real.
    accoes = [nome for nome in pedidas if nome not in CONSULTAS]
    final = accoes[-1] if accoes else (pedidas[-1] if pedidas else None)

    if esperado == "?":
        return "⚠️", f"ambíguo — escolheu {final or 'nenhuma'}"
    if esperado is None:
        return ("✅", "sem ferramentas") if not pedidas else ("❌", f"usou {final}")
    if final is None:
        return "❌", "não usou ferramenta nenhuma"
    if isinstance(esperado, set):
        return ("✅", final) if final in esperado else ("❌", f"escolheu {final}")
    return ("✅", final) if final == esperado else ("❌", f"escolheu {final}")


def main() -> int:
    try:
        settings.validate()
    except ConfigError as exc:
        print(f"Configuração inválida: {exc}")
        return 1

    db.init_db()
    ctx = ToolContext(user_id=999_001, chat_id=999_001, first_name="Miguel")

    print()
    print("Ensaio a seco da escolha de ferramentas (chamadas REAIS à API).")
    print(f"Modelo: {settings.deepseek_model} | {len(CASOS)} casos | custo esperado: poucos cêntimos")
    print("=" * 78)

    acertos = ambiguos = falhas = 0
    total = {"entrada": 0, "cache": 0, "saida": 0}

    for mensagem, esperado in CASOS:
        try:
            pedidas, argumentos, tokens = escolha_do_modelo(ctx, mensagem)
        except Exception as exc:  # noqa: BLE001
            print(f"❌  {mensagem[:44]:<46} erro: {exc}")
            falhas += 1
            continue

        for chave in total:
            total[chave] += tokens[chave]

        simbolo, comentario = avalia(esperado, pedidas)
        acertos += simbolo == "✅"
        ambiguos += simbolo == "⚠️"
        falhas += simbolo == "❌"

        esperado_txt = (
            "conversa" if esperado is None
            else "?" if esperado == "?"
            else "/".join(sorted(esperado)) if isinstance(esperado, set)
            else esperado
        )
        print(f"{simbolo}  {mensagem[:44]:<46} {esperado_txt:<16} {comentario}")
        if simbolo == "❌" and argumentos:
            print(f"     argumentos: {argumentos}")

    print("=" * 78)
    print(f"{acertos} corretos · {ambiguos} ambíguos (informativo) · {falhas} errados")

    if total["entrada"]:
        percentagem = total["cache"] / total["entrada"] * 100
        custo = total["entrada"] * 0.28 / 1e6 + total["saida"] * 0.42 / 1e6
        print(
            f"Tokens: {total['entrada']} entrada ({total['cache']} em cache, "
            f"{percentagem:.0f}%), {total['saida']} saída · custo ≈ ${custo:.4f}"
        )

    if falhas:
        print("\nUm erro aqui costuma significar que a descrição da ferramenta")
        print("em tools.py TOOL_SCHEMAS precisa de ser mais explícita.")
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(main())
