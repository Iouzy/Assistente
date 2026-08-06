"""Teste de fumo: exercita tudo excepto as chamadas reais à API DeepSeek."""
import os
import pathlib
import sys
import tempfile
import time

os.environ.setdefault("TELEGRAM_TOKEN", "123:FAKE")
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-fake")
os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "teste.db")
os.environ["TIMEZONE"] = "Europe/Lisbon"
os.environ["LOG_LEVEL"] = "WARNING"

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import logging
logging.basicConfig(level=logging.WARNING)

import bot  # noqa: E402
import database as db  # noqa: E402
import llm  # noqa: E402
import scheduler  # noqa: E402
import tools  # noqa: E402
from config import settings  # noqa: E402
from tools import ToolContext  # noqa: E402

falhas = []


def check(nome, condicao, detalhe=""):
    marca = "OK " if condicao else "FALHA"
    print(f"[{marca}] {nome}" + (f" -> {detalhe}" if detalhe else ""))
    if not condicao:
        falhas.append(nome)


# --- configuração -----------------------------------------------------------
settings.validate()
check("configuração válida", True)

db.init_db()
check("base de dados inicializada", True)

recebidas = []
scheduler.start(lambda chat_id, texto: recebidas.append((chat_id, texto)))
check("scheduler a correr", scheduler.is_running())

ctx = ToolContext(user_id=42, chat_id=99, first_name="Miguel")

# --- interpretação de datas -------------------------------------------------
for expressao in ["amanhã às 15h", "sexta-feira às 9h30", "daqui a 2 horas",
                  "12/09/2026 18:00", "hoje ao almoço", "meia-noite"]:
    resultado = tools.parse_datetime(expressao)
    check(f"parse {expressao!r}", resultado is not None,
          resultado.isoformat() if resultado else "None")

check("data inválida devolve None", tools.parse_datetime("blá blá blá") is None)

# --- ferramenta 1 -----------------------------------------------------------
agora = tools.execute_tool("get_current_datetime", {}, ctx)
check("get_current_datetime", agora["estado"] == "ok", agora["data_hora_legivel"])

# --- ferramenta 2 -----------------------------------------------------------
r = tools.execute_tool("add_event", {"date": "amanhã às 15h",
                                     "description": "Consulta no dentista"}, ctx)
check("add_event", r["estado"] == "ok", r["evento"]["data_legivel"])
check("add_event agenda lembrete", r["lembrete"]["criado"], r["lembrete"].get("hora_legivel", ""))

r2 = tools.execute_tool("add_event", {"date": "hoje daqui a 5 minutos",
                                      "description": "Chamada urgente"}, ctx)
check("add_event evento próximo", r2["estado"] == "ok")

r3 = tools.execute_tool("add_event", {"date": "não sei quando",
                                      "description": "Coisa"}, ctx)
check("add_event data inválida devolve erro", r3["estado"] == "erro")

r4 = tools.execute_tool("add_event", {"date": "amanhã", "description": ""}, ctx)
check("add_event sem descrição devolve erro", r4["estado"] == "erro")

# --- ferramenta 3 -----------------------------------------------------------
por_data = tools.execute_tool("search_events", {"query": "amanhã"}, ctx)
check("search_events por data", por_data["total"] >= 1, str(por_data["total"]))

por_texto = tools.execute_tool("search_events", {"query": "dentista"}, ctx)
check("search_events por texto", por_texto["total"] == 1)

proximos = tools.execute_tool("search_events", {"query": ""}, ctx)
check("search_events próximos", proximos["total"] >= 2, str(proximos["total"]))

hoje = tools.execute_tool("search_events", {"query": "hoje"}, ctx)
check("search_events hoje", hoje["tipo_de_pesquisa"] == "data", str(hoje["total"]))

vazio = tools.execute_tool("search_events", {"query": "unicórnios"}, ctx)
check("search_events sem resultados", vazio["total"] == 0)

# --- ferramentas 4 e 5 ------------------------------------------------------
n = tools.execute_tool("save_note", {"content": "O código do alarme é 4471"}, ctx)
check("save_note", n["estado"] == "ok", n["nota"]["criada_em"])

busca = tools.execute_tool("search_notes", {"query": "alarme"}, ctx)
check("search_notes", busca["total"] == 1, busca["notas"][0]["conteudo"])

recentes = tools.execute_tool("search_notes", {"query": ""}, ctx)
check("search_notes sem termo", recentes["total"] == 1)

# --- ferramenta 6 -----------------------------------------------------------
lem = tools.execute_tool("set_reminder", {"message": "Beber água",
                                          "time": "daqui a 2 segundos"}, ctx)
check("set_reminder", lem["estado"] == "ok", lem["lembrete"]["hora_legivel"])

passado = tools.execute_tool("set_reminder", {"message": "X", "time": "9:00"}, ctx)
check("set_reminder hora já passada empurra para o futuro",
      passado["estado"] == "ok", passado["lembrete"]["hora_legivel"])

mau = tools.execute_tool("set_reminder", {"message": "X", "time": "xpto"}, ctx)
check("set_reminder hora inválida devolve erro", mau["estado"] == "erro")

# --- ferramenta 7 -----------------------------------------------------------
pendentes = tools.execute_tool("list_reminders", {}, ctx)
check("list_reminders", pendentes["total"] >= 3, str(pendentes["total"]))

# --- despacho robusto -------------------------------------------------------
check("ferramenta inexistente", tools.execute_tool("voar", {}, ctx)["estado"] == "erro")
check("argumentos a mais são ignorados",
      tools.execute_tool("save_note", {"content": "teste", "lixo": 1}, ctx)["estado"] == "ok")
check("argumento em falta devolve erro",
      tools.execute_tool("add_event", {"date": "amanhã"}, ctx)["estado"] == "erro")

# --- disparo real do lembrete ----------------------------------------------
print("... a aguardar o disparo do lembrete (4s)")
time.sleep(4)
check("lembrete disparou e notificou", len(recebidas) >= 1,
      recebidas[0][1].replace("\n", " | ") if recebidas else "nada recebido")
check("lembrete foi para o chat certo", recebidas and recebidas[0][0] == 99)

marcado = [r for r in db.get_pending_reminders() if r["message"] == "Beber água"]
check("lembrete marcado como disparado", not marcado)

# --- restauro após reinício -------------------------------------------------
scheduler.shutdown(wait=True)
recebidas.clear()
scheduler.start(lambda chat_id, texto: recebidas.append((chat_id, texto)))
check("restauro de lembretes pendentes", scheduler.is_running(),
      f"{len(db.get_pending_reminders())} pendentes na BD")

# --- lembrete expirado com tolerância --------------------------------------
from datetime import datetime, timedelta  # noqa: E402
atrasado_id = db.create_reminder(42, 99, "Atrasado",
                                 datetime.now(settings.tzinfo) - timedelta(minutes=5))
antigo_id = db.create_reminder(42, 99, "Muito antigo",
                               datetime.now(settings.tzinfo) - timedelta(days=3))
scheduler.restore_pending_reminders()
time.sleep(12)
textos = " ".join(t for _, t in recebidas)
check("lembrete atrasado recuperado", "Atrasado" in textos)
check("aviso de atraso incluído", "atrasado" in textos.lower())
check("lembrete muito antigo descartado",
      db.get_reminder(antigo_id)["fired"] == 1 and "Muito antigo" not in textos)

# --- memória ----------------------------------------------------------------
db.save_summary(42, "O utilizador chama-se Miguel e trabalha em Aveiro.")
check("resumo guardado e lido", "Aveiro" in (db.get_latest_summary(42) or ""))

prompt = llm.build_system_prompt(ctx)
check("prompt inclui persona", "português europeu" in prompt)
check("prompt inclui data actual", "Data e hora:" in prompt)
check("prompt inclui agenda do dia", "Compromissos de hoje" in prompt or
      "Não há compromissos" in prompt)
check("prompt inclui memória", "Aveiro" in prompt)

llm.append_history(42, "user", "olá")
llm.append_history(42, "assistant", "olá!")
check("histórico em memória", len(llm.get_history(42)) == 2)
llm.reset_history(42)
check("reset do histórico", llm.get_history(42) == [])

# --- preferências -----------------------------------------------------------
db.set_preference(42, "tratamento", "tu")
db.set_preference(42, "tratamento", "você")
check("preferência actualizada (upsert)", db.get_preference(42, "tratamento") == "você")

# --- esquemas das ferramentas ----------------------------------------------
nomes = {s["function"]["name"] for s in tools.TOOL_SCHEMAS}
esperados = {"get_current_datetime", "add_event", "search_events", "save_note",
             "search_notes", "set_reminder", "list_reminders"}
check("esquemas completos", nomes == esperados, ", ".join(sorted(nomes)))
check("esquemas registados no dispatcher", nomes == set(tools._REGISTRY))

import json  # noqa: E402
check("esquemas serializáveis em JSON", bool(json.dumps(tools.TOOL_SCHEMAS)))

# --- utilitários do bot -----------------------------------------------------
blocos = bot._split_message("linha\n" * 3000)
check("mensagens longas são partidas", len(blocos) > 1 and
      all(len(b) <= bot.TELEGRAM_MAX_LENGTH for b in blocos), f"{len(blocos)} blocos")
check("mensagens curtas não são partidas", bot._split_message("olá") == ["olá"])

# --- encerramento -----------------------------------------------------------
scheduler.shutdown(wait=True)
db.close_db()

print()
if falhas:
    print(f"❌ {len(falhas)} falha(s): {', '.join(falhas)}")
    sys.exit(1)
print("✅ Todos os testes passaram.")
