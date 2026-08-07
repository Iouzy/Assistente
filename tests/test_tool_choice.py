"""Verifica a que ferramenta o modelo atribui cada tipo de mensagem.

Ao contrário dos outros testes, este **fala com a API DeepSeek a sério** — é a
única forma de saber se as descrições das ferramentas são claras o suficiente.
Custa poucos cêntimos (cerca de 30 chamadas curtas).

É um ensaio a seco: nenhuma ferramenta que altere dados chega a ser executada.
Corre contra uma base de dados temporária, semeada com uma agenda e umas notas
plausíveis — sem isso o modelo veria tudo vazio e não teria motivo para
consultar nada, e o ensaio media o vazio em vez da escolha de ferramentas.

Quando o modelo pede uma consulta (ver as horas, procurar na agenda), corremos
a ferramenta verdadeira — são todas de leitura — e deixamo-lo continuar. A
acção que ele escolher a seguir é a que fica registada. A base de dados real
nunca é tocada.

Uso:
    python tests/test_tool_choice.py
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

# Em Windows, redirecionar a saída para um ficheiro (`> resultado.txt`) faz o
# Python largar o UTF-8 e usar a codificação local (cp1252), que não sabe
# escrever emojis — e o teste rebentava com UnicodeEncodeError logo na
# primeira linha de resultado. Forçamos UTF-8 na saída.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Base de dados temporária, apagada no fim: os dados reais não são tocados.
_PASTA_TEMPORARIA = tempfile.mkdtemp(prefix="assistente-ensaio-")
os.environ["DATABASE_PATH"] = os.path.join(_PASTA_TEMPORARIA, "ensaio.db")
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

    # --- linha do tempo: coisas que já aconteceram ---
    # Casos inequívocos: passado explícito, com ou sem dia.
    ("we went to the aquarium today", "log_moment"),
    ("fui ao oceanário hoje", "log_moment"),
    ("hoje contaram-me que a fábrica vai fechar", "log_moment"),
    ("Bia went to the dentist yesterday", "log_moment"),
    ("a Bia foi ao dentista ontem", "log_moment"),
    ("watched the new episode tonight", "log_moment"),
    ("vi este filme hoje", "log_moment"),
    ("saw a great film last thursday", "log_moment"),

    # A fronteira delicada: é experiência ou é facto? A persona manda na
    # linha do tempo quando é as duas coisas, mas o modelo é que decide.
    ("aprendi hoje a fazer pão", {"log_moment", "save_note"}),
    ("someone told me the shop closes at 8", {"log_moment", "save_note"}),
    ("I learnt that Ana is moving to Porto", {"log_moment", "save_note"}),

    # Não deve ir para a linha do tempo: factos sem carácter temporal.
    ("the alarm code is 4471", "save_note"),
    ("my shoe size is 43", "save_note"),

    # --- consultar a linha do tempo ---
    ("what happened yesterday?", "search_timeline"),
    ("o que aconteceu ontem?", "search_timeline"),
    ("what did I do last week?", "search_timeline"),
    ("when did we go to the aquarium?", "search_timeline"),

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

# Ferramentas de consulta: são só de leitura, por isso corremos as verdadeiras
# contra a base de dados semeada e deixamos o modelo continuar. O que interessa
# avaliar é a acção que ele toma **a seguir**.
CONSULTAS = {"get_current_datetime", "search_events", "search_notes", "list_reminders",
             "search_timeline"}


def semear(ctx: ToolContext) -> None:
    """Enche a base de dados temporária com dados plausíveis.

    Sem isto, o assistente vê uma agenda vazia e um contexto que diz "nothing
    scheduled" — e deixa de ter motivo para consultar seja o que for. O ensaio
    passava a medir o vazio em vez de medir a escolha de ferramentas.
    """
    agora = datetime.now(settings.tzinfo)
    hoje = agora.replace(hour=0, minute=0, second=0, microsecond=0)
    # Próxima sexta-feira (ou hoje, se hoje for sexta).
    sexta = hoje + timedelta(days=(4 - hoje.weekday()) % 7)

    db.create_event(ctx.user_id, ctx.chat_id, "Team meeting", hoje + timedelta(hours=10))
    db.create_event(ctx.user_id, ctx.chat_id, "Dentist", sexta + timedelta(hours=15))
    db.create_event(ctx.user_id, ctx.chat_id, "Car service", hoje + timedelta(days=9, hours=9))
    db.create_note(ctx.user_id, "office wifi is Torre2024")
    db.create_note(ctx.user_id, "the alarm code is 4471")
    db.create_reminder(
        ctx.user_id, ctx.chat_id, "take the pill", agora + timedelta(hours=6), kind="simple"
    )
    # Linha do tempo já com alguma coisa, pela mesma razão: uma consulta que só
    # pode devolver vazio não distingue quem escolheu bem de quem não escolheu.
    db.create_moment(ctx.user_id, "went to the aquarium with Bia", agora.date() - timedelta(days=3))
    db.create_moment(ctx.user_id, "Bia said the results came back fine", agora.date() - timedelta(days=1))
    db.create_moment(ctx.user_id, "finished the series about the lighthouse", agora.date() - timedelta(days=1))


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
            # Só ferramentas de leitura chegam aqui — nada é alterado.
            try:
                args_consulta = json.loads(c.function.arguments or "{}")
            except json.JSONDecodeError:
                args_consulta = {}
            resultado = tools.execute_tool(c.function.name, args_consulta, ctx)
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
    semear(ctx)

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
        if simbolo == "❌":
            # A cadeia completa é o que permite distinguir "escolheu mal" de
            # "parou cedo depois de uma consulta legítima".
            print(f"     cadeia: {' → '.join(pedidas) if pedidas else '(nenhuma)'}")
            if argumentos:
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


def limpar() -> None:
    """Apaga a base de dados do ensaio — não deixamos lixo na pasta temporária."""
    db.close_db()
    shutil.rmtree(_PASTA_TEMPORARIA, ignore_errors=True)


if __name__ == "__main__":
    try:
        codigo = main()
    finally:
        limpar()
    raise SystemExit(codigo)
