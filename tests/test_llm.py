"""Testa o ciclo de tool calling e a ponte scheduler -> event loop, com mocks."""
import asyncio
import os
import pathlib
import sys
import tempfile
import types

os.environ.setdefault("TELEGRAM_TOKEN", "123:FAKE")
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-fake")
os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "loop.db")
os.environ["TIMEZONE"] = "Europe/Lisbon"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["MAX_HISTORY_MESSAGES"] = "4"
os.environ["HISTORY_KEEP_MESSAGES"] = "2"

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import database as db  # noqa: E402
import llm  # noqa: E402
import main  # noqa: E402
import scheduler  # noqa: E402
from tools import ToolContext  # noqa: E402

falhas = []


def check(nome, cond, detalhe=""):
    print(f"[{'OK ' if cond else 'FALHA'}] {nome}" + (f" -> {detalhe}" if detalhe else ""))
    if not cond:
        falhas.append(nome)


# --------------------------------------------------------------------------
# Cliente DeepSeek falso
# --------------------------------------------------------------------------
def obj(**kwargs):
    return types.SimpleNamespace(**kwargs)


def tool_call(call_id, name, arguments):
    return obj(id=call_id, type="function",
               function=obj(name=name, arguments=arguments))


def resposta(content=None, tool_calls=None):
    return obj(choices=[obj(message=obj(content=content, tool_calls=tool_calls))])


class FakeCompletions:
    """Devolve o guião pela ordem; as chamadas de resumo têm resposta própria."""

    def __init__(self, guiao, resumo="A Ana mora no Porto e trabalha em design."):
        self.guiao = list(guiao)
        self.pedidos = []
        self.resumo = resumo
        self.resumos_pedidos = 0

    def create(self, **kwargs):
        sistema = kwargs["messages"][0]["content"]
        if sistema.startswith("Resume a conversa"):
            self.resumos_pedidos += 1
            return resposta(content=self.resumo)
        self.pedidos.append(kwargs)
        return self.guiao.pop(0)


class FakeClient:
    def __init__(self, guiao):
        self.chat = obj(completions=FakeCompletions(guiao))


def instalar(guiao):
    cliente = FakeClient(guiao)
    llm.get_client = lambda: cliente
    return cliente


db.init_db()
scheduler.start(lambda chat_id, texto: None)
ctx = ToolContext(user_id=7, chat_id=77, first_name="Ana")

# --------------------------------------------------------------------------
# 1. Resposta directa, sem ferramentas
# --------------------------------------------------------------------------
cliente = instalar([resposta(content="Olá, Ana! Como estás?")])
r = llm.generate_reply(ctx, "olá")
check("resposta sem ferramentas", r == "Olá, Ana! Como estás?", r)
check("uma só chamada à API", len(cliente.chat.completions.pedidos) == 1)

pedido = cliente.chat.completions.pedidos[0]
check("tools enviadas no formato OpenAI",
      len(pedido["tools"]) == 7 and pedido["tools"][0]["type"] == "function")
check("tool_choice=auto", pedido["tool_choice"] == "auto")
check("prompt de sistema presente", pedido["messages"][0]["role"] == "system")
check("modelo correcto", pedido["model"] == "deepseek-chat")

# --------------------------------------------------------------------------
# 2. Ciclo com ferramenta: add_event -> resultado -> resposta final
# --------------------------------------------------------------------------
cliente = instalar([
    resposta(tool_calls=[tool_call("call_1", "add_event",
             '{"date": "amanhã às 15h", "description": "Dentista"}')]),
    resposta(content="✅ Marcado! Dentista amanhã às 15:00. Aviso-te às 14:45."),
])
r = llm.generate_reply(ctx, "marca dentista amanhã às 15h")
check("resposta final após ferramenta", "Marcado" in r, r)
check("duas chamadas à API", len(cliente.chat.completions.pedidos) == 2)

segundas_msgs = cliente.chat.completions.pedidos[1]["messages"]
check("mensagem assistant com tool_calls",
      segundas_msgs[-2]["role"] == "assistant" and "tool_calls" in segundas_msgs[-2])
check("resultado devolvido com role=tool",
      segundas_msgs[-1]["role"] == "tool" and
      segundas_msgs[-1]["tool_call_id"] == "call_1")
check("resultado da ferramenta em JSON",
      '"estado": "ok"' in segundas_msgs[-1]["content"])
check("evento realmente gravado", len(db.get_upcoming_events(7, __import__("datetime")
      .datetime.now(__import__("config").settings.tzinfo))) == 1)

# --------------------------------------------------------------------------
# 3. Várias ferramentas na mesma resposta (paralelas)
# --------------------------------------------------------------------------
cliente = instalar([
    resposta(tool_calls=[
        tool_call("a", "get_current_datetime", "{}"),
        tool_call("b", "search_events", '{"query": "amanhã"}'),
    ]),
    resposta(content="Amanhã tens o dentista às 15:00."),
])
r = llm.generate_reply(ctx, "o que tenho amanhã?")
check("tool calls paralelas", "dentista" in r.lower(), r)
msgs = cliente.chat.completions.pedidos[1]["messages"]
check("dois resultados devolvidos",
      [m["role"] for m in msgs[-2:]] == ["tool", "tool"])

# --------------------------------------------------------------------------
# 4. Argumentos JSON inválidos não partem nada
# --------------------------------------------------------------------------
cliente = instalar([
    resposta(tool_calls=[tool_call("c", "search_notes", "{isto não é json")]),
    resposta(content="Não encontrei notas."),
])
r = llm.generate_reply(ctx, "que notas tenho?")
check("JSON inválido tolerado", r == "Não encontrei notas.", r)

# --------------------------------------------------------------------------
# 5. Limite de rondas de ferramentas
# --------------------------------------------------------------------------
ciclo = [resposta(tool_calls=[tool_call(f"x{i}", "get_current_datetime", "{}")])
         for i in range(5)]
cliente = instalar(ciclo + [resposta(content="Pronto, aqui está a resposta.")])
r = llm.generate_reply(ctx, "loop infinito")
check("limite de rondas força resposta final", "Pronto" in r, r)
check("chamada final sem tools",
      "tools" not in cliente.chat.completions.pedidos[-1])

# --------------------------------------------------------------------------
# 6. Erros da API viram AssistantError legível
# --------------------------------------------------------------------------
class ClienteQueFalha:
    class _C:
        def create(self, **kwargs):
            raise ConnectionError("rede em baixo")
    chat = obj(completions=_C())


llm.get_client = lambda: ClienteQueFalha()
try:
    llm.generate_reply(ctx, "olá")
    check("erro da API tratado", False, "não levantou")
except llm.AssistantError as exc:
    check("erro da API vira AssistantError", "erro" in str(exc).lower(), str(exc))
except Exception as exc:
    check("erro da API vira AssistantError", False, f"{type(exc).__name__}: {exc}")

# --------------------------------------------------------------------------
# 7. Compactação da memória (MAX=4, KEEP=2)
# --------------------------------------------------------------------------
llm.reset_history(7)
cliente = instalar([resposta(content=f"resposta {i}") for i in range(6)])
for i in range(3):                       # 3 turnos = 6 mensagens > MAX(4)
    llm.generate_reply(ctx, f"mensagem {i}")

check("histórico compactado", len(llm.get_history(7)) <= 4,
      f"{len(llm.get_history(7))} mensagens")
check("summarize_memory foi chamado",
      cliente.chat.completions.resumos_pedidos >= 1,
      f"{cliente.chat.completions.resumos_pedidos} resumo(s)")
resumo = db.get_latest_summary(7)
check("resumo gravado na base de dados", resumo is not None and "Porto" in resumo, resumo or "")
check("resumo entra no prompt de sistema", "Porto" in llm.build_system_prompt(ctx))

# --------------------------------------------------------------------------
# 8. Ponte scheduler (thread) -> event loop do Telegram
# --------------------------------------------------------------------------
enviadas = []


class FakeBot:
    async def send_message(self, chat_id, text, parse_mode=None):
        await asyncio.sleep(0)
        enviadas.append((chat_id, text))


async def cenario():
    app = obj(bot=FakeBot())
    loop = asyncio.get_running_loop()
    notificar = main.build_notifier(app, loop)

    scheduler.shutdown(wait=True)
    scheduler.start(notificar)

    from datetime import datetime, timedelta
    from config import settings as s
    rid = db.create_reminder(7, 77, "Regar as plantas",
                             datetime.now(s.tzinfo) + timedelta(seconds=2))
    scheduler.schedule_reminder(rid, datetime.now(s.tzinfo) + timedelta(seconds=2))
    await asyncio.sleep(4)          # o loop continua livre enquanto espera
    return rid


rid = asyncio.run(cenario())
check("scheduler enviou via event loop", len(enviadas) == 1,
      enviadas[0][1].replace("\n", " | ") if enviadas else "nada")
check("mensagem no chat correcto", enviadas and enviadas[0][0] == 77)
check("lembrete marcado como disparado", db.get_reminder(rid)["fired"] == 1)

scheduler.shutdown(wait=True)
db.close_db()

print()
if falhas:
    print(f"❌ {len(falhas)} falha(s): {', '.join(falhas)}")
    sys.exit(1)
print("✅ Todos os testes do ciclo LLM passaram.")
