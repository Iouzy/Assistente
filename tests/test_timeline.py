"""Testa a linha do tempo: registo por dia, consulta e apagar.

O ponto delicado é a interpretação das datas. Aqui «quinta» quer dizer a
quinta que passou, ao contrário do resto do assistente, onde quer dizer a
próxima. Não gasta um único token da API.

Uso:  python tests/test_timeline.py
"""
import os
import pathlib
import sys
import tempfile
from datetime import date, datetime, timedelta

# Em Windows, redirecionar a saída para um ficheiro (`> resultado.txt`) faz o
# Python largar o UTF-8 e usar a codificação local (cp1252), que não sabe
# escrever emojis — e o teste rebentava com UnicodeEncodeError logo na
# primeira linha de resultado. Forçamos UTF-8 na saída.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

os.environ.setdefault("TELEGRAM_TOKEN", "123:FAKE")
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-fake")
os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "timeline.db")
os.environ["TIMEZONE"] = "Europe/Lisbon"
os.environ["LOG_LEVEL"] = "CRITICAL"

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import database as db  # noqa: E402
import tools  # noqa: E402
from config import settings  # noqa: E402
from tools import ToolContext  # noqa: E402

falhas = []


def check(nome, cond, detalhe=""):
    print(f"[{'OK ' if cond else 'FALHA'}] {nome}" + (f" -> {detalhe}" if detalhe else ""))
    if not cond:
        falhas.append(nome)


db.init_db()
ctx = ToolContext(user_id=7, chat_id=7, first_name="Miguel")
OUTRO = ToolContext(user_id=8, chat_id=8, first_name="Outro")
HOJE = datetime.now(settings.tzinfo).date()

# --- datas do passado -------------------------------------------------------
check("sem data assume hoje", tools.parse_day("") == HOJE)
check("«ontem» é ontem", tools.parse_day("ontem") == HOJE - timedelta(days=1))
check("«yesterday» é ontem", tools.parse_day("yesterday") == HOJE - timedelta(days=1))
check("«hoje» é hoje", tools.parse_day("hoje") == HOJE)

# O essencial: um dia da semana refere-se ao passado, não ao futuro.
quinta = tools.parse_day("quinta")
check("«quinta» cai no passado", quinta is not None and quinta <= HOJE, str(quinta))
check("«quinta» é mesmo uma quinta", quinta is not None and quinta.weekday() == 3, str(quinta))
thursday = tools.parse_day("last thursday")
check("«last thursday» cai no passado", thursday is not None and thursday <= HOJE, str(thursday))
check("«last thursday» é a mesma coisa que «thursday»", thursday == tools.parse_day("thursday"))
# O qualificador aparece antes em inglês e depois em português.
check("«quinta passada» cai no passado",
      tools.parse_day("quinta passada") == tools.parse_day("quinta"))
check("«última sexta» cai no passado",
      (lambda d: d is not None and d <= HOJE and d.weekday() == 4)(tools.parse_day("última sexta")),
      str(tools.parse_day("última sexta")))
check("«3 dias atrás» conta para trás",
      tools.parse_day("3 dias atrás") == HOJE - timedelta(days=3))
check("«2 days ago» conta para trás",
      tools.parse_day("2 days ago") == HOJE - timedelta(days=2))

# E o assistente continua a marcar compromissos para a frente.
futuro = tools.parse_datetime("quinta")
check("o parser dos compromissos continua a olhar para a frente",
      futuro is not None and futuro.date() >= HOJE, str(futuro))
check("data impossível devolve None", tools.parse_day("um dia qualquer da vida") is None)

# --- registar ---------------------------------------------------------------
r = tools.execute_tool("log_moment", {"content": "fui ao oceanário", "date": ""}, ctx)
check("regista sem data", r["status"] == "ok", str(r))
check("fica no dia de hoje", r["moment"]["day"] == tools.format_day(HOJE), r["moment"]["day"])

r = tools.execute_tool("log_moment", {"content": "a Bia foi ao dentista", "date": "ontem"}, ctx)
check("regista com dia passado", r["status"] == "ok", str(r))
ontem_id = r["moment"]["id"]

r = tools.execute_tool("log_moment", {"content": "", "date": ""}, ctx)
check("conteúdo vazio é recusado", r["status"] == "error")

# Vários acontecimentos no mesmo dia.
tools.execute_tool("log_moment", {"content": "a Bia disse que lhe deram alta", "date": "ontem"}, ctx)
de_ontem = db.get_moments_between(ctx.user_id, HOJE - timedelta(days=1), HOJE - timedelta(days=1))
check("vários acontecimentos no mesmo dia", len(de_ontem) == 2, str(len(de_ontem)))

# --- consultar --------------------------------------------------------------
r = tools.execute_tool("search_timeline", {"query": "ontem"}, ctx)
check("consulta por dia", r["matched_by"] == "day" and r["count"] == 2, str(r))

r = tools.execute_tool("search_timeline", {"query": ""}, ctx)
check("consulta sem filtro devolve os recentes", r["count"] == 3, str(r["count"]))
check("vem do mais recente para o mais antigo",
      r["moments"][0]["day"] >= r["moments"][-1]["day"],
      f"{r['moments'][0]['day']} .. {r['moments'][-1]['day']}")

r = tools.execute_tool("search_timeline", {"query": "semana passada"}, ctx)
check("consulta por período", r["matched_by"] == "period" and r["count"] == 3, str(r))

r = tools.execute_tool("search_timeline", {"query": "últimos 3 dias"}, ctx)
check("consulta «últimos N dias»", r["matched_by"] == "period" and r["count"] == 3, str(r))

r = tools.execute_tool("search_timeline", {"query": "oceanário"}, ctx)
check("consulta por palavra", r["matched_by"] == "text" and r["count"] == 1, str(r))

r = tools.execute_tool("search_timeline", {"query": "coisa que nunca aconteceu"}, ctx)
check("consulta sem resultados não rebenta", r["status"] == "ok" and r["count"] == 0)

# --- isolamento entre utilizadores -----------------------------------------
r = tools.execute_tool("search_timeline", {"query": ""}, OUTRO)
check("outra pessoa não vê a linha do tempo alheia", r["count"] == 0, str(r))
check("nem a consegue apagar",
      tools.execute_tool("delete_item", {"kind": "moment", "id": ontem_id}, OUTRO)["status"] == "error")
check("o acontecimento continua lá", db.get_moments_between(
    ctx.user_id, HOJE - timedelta(days=1), HOJE - timedelta(days=1)) != [])

# --- apagar -----------------------------------------------------------------
r = tools.execute_tool("delete_item", {"kind": "moment", "id": ontem_id}, ctx)
check("o dono apaga o seu acontecimento", r["status"] == "ok", str(r))
check("desapareceu mesmo", len(db.get_moments_between(
    ctx.user_id, HOJE - timedelta(days=1), HOJE - timedelta(days=1))) == 1)
check("apagar duas vezes dá erro",
      tools.execute_tool("delete_item", {"kind": "moment", "id": ontem_id}, ctx)["status"] == "error")

# --- a linha do tempo não é memória de conversa -----------------------------
# O /forget all limpa os resumos; o que foi registado de propósito fica.
db.save_summary(ctx.user_id, "um resumo qualquer")
db.delete_summaries(ctx.user_id)
check("/forget all não mexe na linha do tempo", len(db.list_moments(ctx.user_id)) == 2)

# --- dias guardados sem fuso (evita a troca de ordem na mudança de hora) ----
linha = db.list_moments(ctx.user_id)[0]
check("o dia é guardado como YYYY-MM-DD", len(linha["happened_on"]) == 10, linha["happened_on"])
check("sem hora nem deslocamento",
      "+" not in linha["happened_on"] and "T" not in linha["happened_on"])
check("é uma data válida", date.fromisoformat(linha["happened_on"]) is not None)

# --- formatação -------------------------------------------------------------
check("formata o dia por extenso",
      tools.format_day("2026-08-06") == "Thursday, 6 August 2026",
      tools.format_day("2026-08-06"))
check("data inválida não rebenta a formatação", tools.format_day("nem-uma-data") == "nem-uma-data")

print()
if falhas:
    print(f"❌ {len(falhas)} falha(s): {', '.join(falhas)}")
    sys.exit(1)
print("✅ Todos os testes da linha do tempo passaram.")
