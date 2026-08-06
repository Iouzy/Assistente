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
check("get_current_datetime", agora["status"] == "ok", agora["now"])

# --- ferramenta 2 -----------------------------------------------------------
r = tools.execute_tool("add_event", {"date": "amanhã às 15h",
                                     "description": "Consulta no dentista"}, ctx)
check("add_event", r["status"] == "ok", r["event"]["when"])
check("add_event agenda lembrete", r["reminder"]["created"], r["reminder"].get("at", ""))

r2 = tools.execute_tool("add_event", {"date": "hoje daqui a 5 minutos",
                                      "description": "Chamada urgente"}, ctx)
check("add_event evento próximo", r2["status"] == "ok")

r3 = tools.execute_tool("add_event", {"date": "não sei quando",
                                      "description": "Coisa"}, ctx)
check("add_event data inválida devolve erro", r3["status"] == "error")

r4 = tools.execute_tool("add_event", {"date": "amanhã", "description": ""}, ctx)
check("add_event sem descrição devolve erro", r4["status"] == "error")

# --- ferramenta 3 -----------------------------------------------------------
por_data = tools.execute_tool("search_events", {"query": "amanhã"}, ctx)
check("search_events por data", por_data["count"] >= 1, str(por_data["count"]))

por_texto = tools.execute_tool("search_events", {"query": "dentista"}, ctx)
check("search_events por texto", por_texto["count"] == 1)

proximos = tools.execute_tool("search_events", {"query": ""}, ctx)
check("search_events próximos", proximos["count"] >= 2, str(proximos["count"]))

hoje = tools.execute_tool("search_events", {"query": "hoje"}, ctx)
check("search_events hoje", hoje["matched_by"] == "date", str(hoje["count"]))

vazio = tools.execute_tool("search_events", {"query": "unicórnios"}, ctx)
check("search_events sem resultados", vazio["count"] == 0)

# --- ferramentas 4 e 5 ------------------------------------------------------
n = tools.execute_tool("save_note", {"content": "O código do alarme é 4471"}, ctx)
check("save_note", n["status"] == "ok", n["note"]["saved"])

busca = tools.execute_tool("search_notes", {"query": "alarme"}, ctx)
check("search_notes", busca["count"] == 1, busca["notes"][0]["content"])

recentes = tools.execute_tool("search_notes", {"query": ""}, ctx)
check("search_notes sem termo", recentes["count"] == 1)

# --- ferramenta 6 -----------------------------------------------------------
lem = tools.execute_tool("set_reminder", {"message": "Beber água",
                                          "time": "daqui a 2 segundos"}, ctx)
check("set_reminder", lem["status"] == "ok", lem["reminder"]["at"])

passado = tools.execute_tool("set_reminder", {"message": "X", "time": "9:00"}, ctx)
check("set_reminder hora já passada empurra para o futuro",
      passado["status"] == "ok", passado["reminder"]["at"])

mau = tools.execute_tool("set_reminder", {"message": "X", "time": "xpto"}, ctx)
check("set_reminder hora inválida devolve erro", mau["status"] == "error")

# --- ferramenta 7 -----------------------------------------------------------
pendentes = tools.execute_tool("list_reminders", {}, ctx)
check("list_reminders", pendentes["count"] >= 3, str(pendentes["count"]))

# --- despacho robusto -------------------------------------------------------
check("ferramenta inexistente", tools.execute_tool("voar", {}, ctx)["status"] == "error")
check("argumentos a mais são ignorados",
      tools.execute_tool("save_note", {"content": "teste", "lixo": 1}, ctx)["status"] == "ok")
check("argumento em falta devolve erro",
      tools.execute_tool("add_event", {"date": "amanhã"}, ctx)["status"] == "error")

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

# O prompt de sistema tem de ser 100% estável entre turnos, senão a cache de
# prefixo da DeepSeek nunca acerta. Todo o conteúdo volátil vive no bloco de
# contexto, que segue colado à última mensagem.
prompt = llm.build_system_prompt(ctx)
check("prompt de sistema inclui persona", "personal assistant on Telegram" in prompt)
check("prompt de sistema NÃO tem data/hora", "at 1" not in prompt and "2026" not in prompt)
check("prompt de sistema NÃO tem memória", "Aveiro" not in prompt)

import time as _time  # noqa: E402
primeiro = llm.build_system_prompt(ctx)
_time.sleep(1.1)
check("prompt de sistema é idêntico 1s depois", primeiro == llm.build_system_prompt(ctx))

bloco = llm.build_context_block(ctx.user_id)
check("bloco de contexto tem data actual", "[context: now" in bloco)
check("bloco de contexto tem agenda do dia",
      "Today:" in bloco or "Nothing scheduled today" in bloco)
check("bloco de contexto tem memória", "Aveiro" in bloco)

llm.append_history(42, "user", "olá")
llm.append_history(42, "assistant", "olá!")
check("histórico em memória", len(llm.get_history(42)) == 2)
llm.reset_history(42)
check("reset do histórico", llm.get_history(42) == [])

# --- apagar (ferramenta delete_item) ---------------------------------------
nota_id = tools.execute_tool("save_note", {"content": "nota descartável"}, ctx)["note"]["id"]
r = tools.execute_tool("delete_item", {"kind": "note", "id": nota_id}, ctx)
check("delete_item apaga nota", r["status"] == "ok")
check("nota desapareceu mesmo",
      tools.execute_tool("search_notes", {"query": "descartável"}, ctx)["count"] == 0)

check("delete_item recusa id inexistente",
      tools.execute_tool("delete_item", {"kind": "note", "id": 99999}, ctx)["status"] == "error")
check("delete_item recusa tipo inválido",
      tools.execute_tool("delete_item", {"kind": "planeta", "id": 1}, ctx)["status"] == "error")

# Apagar um evento tem de levar o lembrete e o respectivo job atrás.
novo = tools.execute_tool("add_event", {"date": "amanhã às 11h",
                                        "description": "Reunião a cancelar"}, ctx)
ev_id = novo["event"]["id"]
check("evento criado com lembrete", len(db.get_reminders_for_event(ev_id)) == 1)
rem_id = db.get_reminders_for_event(ev_id)[0]["id"]
tools.execute_tool("delete_item", {"kind": "event", "id": ev_id}, ctx)
check("evento apagado", db.get_event(ev_id) is None)
check("lembrete do evento apagado em cascata", db.get_reminder(rem_id) is None)
check("job do lembrete cancelado", not scheduler.cancel_reminder(rem_id))

# --- remarcar (ferramenta update_event) ------------------------------------
alvo = tools.execute_tool("add_event", {"date": "amanhã às 9h",
                                        "description": "Dentista"}, ctx)["event"]["id"]
antigo_rem = db.get_reminders_for_event(alvo)[0]["id"]

r = tools.execute_tool("update_event", {"id": alvo, "date": "amanhã às 16h"}, ctx)
check("update_event muda a hora", r["status"] == "ok" and "16:00" in r["event"]["when"],
      r.get("event", {}).get("when", r.get("error", "")))
check("update_event mantém a descrição", r["event"]["description"] == "Dentista")
check("update_event substitui o lembrete antigo", db.get_reminder(antigo_rem) is None)
check("update_event cria lembrete novo", len(db.get_reminders_for_event(alvo)) == 1)
check("novo lembrete é 15 min antes", "15:45" in r["reminder"]["at"], r["reminder"]["at"])

r = tools.execute_tool("update_event", {"id": alvo, "description": "Dentista (Dr. Silva)"}, ctx)
check("update_event muda só a descrição",
      r["event"]["description"] == "Dentista (Dr. Silva)" and "16:00" in r["event"]["when"])

check("update_event sem alterações devolve erro",
      tools.execute_tool("update_event", {"id": alvo}, ctx)["status"] == "error")
check("update_event recusa evento inexistente",
      tools.execute_tool("update_event", {"id": 99999, "date": "amanhã"}, ctx)["status"] == "error")
check("update_event recusa data ilegível",
      tools.execute_tool("update_event", {"id": alvo, "date": "xpto"}, ctx)["status"] == "error")

# --- preferências como ferramenta ------------------------------------------
r = tools.execute_tool("set_preference", {"key": "call_me", "value": "Mike"}, ctx)
check("set_preference guarda", r["status"] == "ok" and
      db.get_preference(ctx.user_id, "call_me") == "Mike")
check("preferência aparece no bloco de contexto",
      "call_me=Mike" in llm.build_context_block(ctx.user_id))
r = tools.execute_tool("set_preference", {"key": "call_me", "value": ""}, ctx)
check("set_preference com valor vazio remove",
      r["status"] == "ok" and db.get_preference(ctx.user_id, "call_me") is None)
check("set_preference sem chave devolve erro",
      tools.execute_tool("set_preference", {"key": "", "value": "x"}, ctx)["status"] == "error")

# --- preferências -----------------------------------------------------------
db.set_preference(42, "tratamento", "tu")
db.set_preference(42, "tratamento", "você")
check("preferência actualizada (upsert)", db.get_preference(42, "tratamento") == "você")

# --- esquemas das ferramentas ----------------------------------------------
nomes = {s["function"]["name"] for s in tools.TOOL_SCHEMAS}
esperados = {"get_current_datetime", "add_event", "search_events", "save_note",
             "search_notes", "set_reminder", "list_reminders",
             "delete_item", "update_event", "set_preference"}
check("esquemas completos", nomes == esperados, ", ".join(sorted(nomes)))
check("esquemas registados no dispatcher", nomes == set(tools._REGISTRY))

import json  # noqa: E402
check("esquemas serializáveis em JSON", bool(json.dumps(tools.TOOL_SCHEMAS)))

# --- utilitários do bot -----------------------------------------------------
blocos = bot._split_message("linha\n" * 3000)
check("mensagens longas são partidas", len(blocos) > 1 and
      all(len(b) <= bot.TELEGRAM_MAX_LENGTH for b in blocos), f"{len(blocos)} blocos")
check("mensagens curtas não são partidas", bot._split_message("olá") == ["olá"])

# --- língua e memória de longo prazo ----------------------------------------
bloco = llm.build_context_block(ctx.user_id)
check("bloco de contexto força o inglês", bloco.rstrip().endswith("[answer in English]"), bloco.splitlines()[-1])
check("regra da língua no topo da persona",
      "LANGUAGE" in llm.build_system_prompt(ctx).splitlines()[2])

db.save_summary(ctx.user_id, "resumo antigo em português")
check("resumos podem ser apagados", db.delete_summaries(ctx.user_id) >= 1)
check("memória de longo prazo vazia depois", db.get_latest_summary(ctx.user_id) is None)
check("bloco de contexto sem memória não parte",
      "[answer in English]" in llm.build_context_block(ctx.user_id))

# --- controlo de acesso -----------------------------------------------------
import asyncio  # noqa: E402
import dataclasses  # noqa: E402
import types  # noqa: E402

from telegram.ext import ApplicationHandlerStop  # noqa: E402


class _BotFalso:
    def __init__(self):
        self.enviadas = []

    async def send_message(self, chat_id, text, **kwargs):
        self.enviadas.append((chat_id, text))


def _actualizacao(uid):
    return types.SimpleNamespace(
        effective_user=types.SimpleNamespace(id=uid, first_name="X", username="x"),
        effective_chat=types.SimpleNamespace(id=uid),
    )


def _porteiro_deixa_passar(uid):
    falso = _BotFalso()
    try:
        asyncio.run(bot.guard_access(_actualizacao(uid), types.SimpleNamespace(bot=falso)))
        return True, falso
    except ApplicationHandlerStop:
        return False, falso


_original = bot.settings

# --- modo A: lista fixa no .env ---
bot.settings = dataclasses.replace(_original, allowed_user_ids=frozenset({111, 222}))
passou, _ = _porteiro_deixa_passar(111)
check("porteiro deixa passar id autorizado", passou)
passou, falso = _porteiro_deixa_passar(999)
check("porteiro bloqueia id estranho", not passou)
check("estranho recebe explicação", falso.enviadas and "private assistant" in falso.enviadas[0][1])

# --- modo B: sem lista no .env, o primeiro a falar reclama o bot ---
bot.settings = dataclasses.replace(_original, allowed_user_ids=frozenset())
bot.refresh_access_cache()
check("base de dados começa sem dono", bot.autorizados() == set())

passou, falso = _porteiro_deixa_passar(555)
check("primeiro utilizador é deixado entrar", passou)
check("primeiro utilizador fica dono", 555 in bot.autorizados(), str(bot.autorizados()))
check("dono é avisado de que o bot é dele",
      falso.enviadas and "now yours" in falso.enviadas[0][1])
check("marcado como dono na base de dados",
      [e for e in db.list_access() if e["user_id"] == 555][0]["is_owner"] == 1)

passou, _ = _porteiro_deixa_passar(556)
check("segundo utilizador já é bloqueado", not passou)

# --- modo B: partilhar com mais pessoas ---
db.grant_access(556, "Ana")
bot.refresh_access_cache()
passou, _ = _porteiro_deixa_passar(556)
check("convidado passa a entrar", passou)
check("convidado não é dono",
      [e for e in db.list_access() if e["user_id"] == 556][0]["is_owner"] == 0)

check("convidado pode ser retirado", db.revoke_access(556))
bot.refresh_access_cache()
passou, _ = _porteiro_deixa_passar(556)
check("convidado retirado volta a ser bloqueado", not passou)
check("dono NÃO pode ser retirado", not db.revoke_access(555))

bot.settings = _original
bot.refresh_access_cache()

# --- encerramento -----------------------------------------------------------
scheduler.shutdown(wait=True)
db.close_db()

print()
if falhas:
    print(f"❌ {len(falhas)} falha(s): {', '.join(falhas)}")
    sys.exit(1)
print("✅ Todos os testes passaram.")
