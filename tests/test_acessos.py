"""Testa a gestão de permissões usada pelos painéis de controlo (Windows e Linux).

Não abre janela nenhuma: o `acessos.py` é só biblioteca-padrão (sqlite3 e
ficheiros), por isso corre em qualquer sistema.
"""
import os
import pathlib
import sqlite3
import sys
import tempfile

# Em Windows, redirecionar a saída para um ficheiro (`> resultado.txt`) faz o
# Python largar o UTF-8 e usar a codificação local (cp1252), que não sabe
# escrever emojis — e o teste rebentava com UnicodeEncodeError logo na
# primeira linha de resultado. Forçamos UTF-8 na saída.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import acessos  # noqa: E402

falhas = []


def check(nome, condicao, detalhe=""):
    marca = "OK " if condicao else "FALHA"
    print(f"[{marca}] {nome}" + (f" -> {detalhe}" if detalhe else ""))
    if not condicao:
        falhas.append(nome)


pasta = pathlib.Path(tempfile.mkdtemp())

# --- leitura do .env --------------------------------------------------------
env_file = pasta / ".env"
env_file.write_text(
    "# comentário\n"
    "TELEGRAM_TOKEN=123:FAKE\n"
    'DATABASE_PATH="dados/assistente.db"\n'
    "export ALLOWED_USER_IDS=111, 222 ;333\n"
    "VAZIA=\n"
    "LOG_LEVEL=INFO # com comentário\n",
    encoding="utf-8",
)
env = acessos.ler_env(env_file)
check("lê pares chave/valor", env.get("TELEGRAM_TOKEN") == "123:FAKE")
check("tira as aspas", env.get("DATABASE_PATH") == "dados/assistente.db", env.get("DATABASE_PATH"))
check("ignora o prefixo export", "ALLOWED_USER_IDS" in env)
check("ignora comentário no fim da linha", env.get("LOG_LEVEL") == "INFO", env.get("LOG_LEVEL"))
check("ficheiro inexistente devolve vazio", acessos.ler_env(pasta / "nao-existe") == {})

check("lê ids separados por vírgula e ponto-e-vírgula",
      acessos.ler_ids(env["ALLOWED_USER_IDS"]) == [111, 222, 333])
check("ignora ids inválidos", acessos.ler_ids("1, abc, 2, ") == [1, 2])
check("lista vazia", acessos.ler_ids("  ") == [])

# --- lista fixa (ALLOWED_USER_IDS) ------------------------------------------
os.environ.pop("ALLOWED_USER_IDS", None)
ids, origem = acessos.lista_fixa(env)
check("detecta a lista fixa do .env", (ids, origem) == ([111, 222, 333], "ficheiro .env"), origem)

os.environ["ALLOWED_USER_IDS"] = "999"
ids, origem = acessos.lista_fixa(env)
check("o ambiente do sistema ganha ao .env", (ids, origem) == ([999], "sistema"), origem)
os.environ.pop("ALLOWED_USER_IDS")

check("sem lista fixa não há aviso", acessos.lista_fixa({}) == ([], ""))

# --- esvaziar a lista fixa --------------------------------------------------
crlf = pasta / "crlf.env"
crlf.write_bytes(b"TELEGRAM_TOKEN=123:FAKE\r\nALLOWED_USER_IDS=111,222\r\nLOG_LEVEL=INFO\r\n")
apanhados = acessos.esvaziar_lista_fixa(crlf)
check("devolve os ids que estavam no ficheiro", apanhados == [111, 222], str(apanhados))
depois = crlf.read_bytes()
check("esvazia só a linha certa",
      depois == b"TELEGRAM_TOKEN=123:FAKE\r\nALLOWED_USER_IDS=\r\nLOG_LEVEL=INFO\r\n", str(depois))
check("guarda cópia de segurança", (pasta / "crlf.env.bak").exists())

sem_variavel = pasta / "sem.env"
sem_variavel.write_text("TELEGRAM_TOKEN=123:FAKE\n", encoding="utf-8")
check("ficheiro sem a variável devolve lista vazia", acessos.esvaziar_lista_fixa(sem_variavel) == [])
check("e fica igual", sem_variavel.read_text(encoding="utf-8") == "TELEGRAM_TOKEN=123:FAKE\n")

try:
    acessos.esvaziar_lista_fixa(pasta / "nao-existe.env")
    check("ficheiro em falta dá erro claro", False)
except acessos.ErroAcesso:
    check("ficheiro em falta dá erro claro", True)

# --- caminho da base de dados -----------------------------------------------
check("caminho relativo é resolvido a partir da raiz",
      acessos.caminho_base_dados({"DATABASE_PATH": "assistente.db"})
      == acessos.RAIZ / "assistente.db")
absoluto = str(pasta / "outra.db")
check("caminho absoluto é respeitado",
      acessos.caminho_base_dados({"DATABASE_PATH": absoluto}) == pathlib.Path(absoluto))
check("sem configuração usa assistente.db",
      acessos.caminho_base_dados({}).name == "assistente.db")

# --- base de dados ----------------------------------------------------------
bd = pasta / "acesso.db"
conexao = acessos.ligar(bd)
check("cria a tabela numa base de dados nova", acessos.listar(conexao) == [])

dono = acessos.adicionar(conexao, 111, "Miguel")
check("o primeiro da lista fica dono", dono)
check("segundo utilizador não fica dono", acessos.adicionar(conexao, 222, "Ana") is False)

lista = acessos.listar(conexao)
check("os dois estão na lista", [r["user_id"] for r in lista] == [111, 222], str(lista))
check("o nome é guardado", lista[1]["label"] == "Ana")
check("o dono vem primeiro", lista[0]["is_owner"] == 1 and lista[1]["is_owner"] == 0)

acessos.adicionar(conexao, 222, "Ana Maria")
lista = acessos.listar(conexao)
check("adicionar de novo só actualiza o nome",
      len(lista) == 2 and lista[1]["label"] == "Ana Maria")
check("adicionar de novo não rouba a coroa ao dono", lista[0]["user_id"] == 111)

check("o dono não pode ser removido", acessos.remover(conexao, 111) is False)
check("o dono continua lá", any(r["user_id"] == 111 for r in acessos.listar(conexao)))

check("passar a coroa a alguém de fora falha", acessos.definir_dono(conexao, 777) is False)
check("passar a coroa funciona", acessos.definir_dono(conexao, 222))
lista = acessos.listar(conexao)
check("só há um dono", [r["user_id"] for r in lista if r["is_owner"]] == [222], str(lista))
check("o antigo dono já pode ser removido", acessos.remover(conexao, 111))
check("ficou só um", [r["user_id"] for r in acessos.listar(conexao)] == [222])

acessos.importar(conexao, [333, 444])
check("importar traz os ids todos",
      sorted(r["user_id"] for r in acessos.listar(conexao)) == [222, 333, 444])

# A lista é lida por outro processo (o bot) enquanto o painel escreve.
outra = sqlite3.connect(str(bd))
outra.row_factory = sqlite3.Row
lidos = {linha["user_id"] for linha in outra.execute("SELECT user_id FROM access")}
check("outro processo vê as alterações já gravadas", lidos == {222, 333, 444}, str(lidos))
outra.close()

acessos.adicionar(conexao, -100123, "grupo")
check("aceita ids negativos (grupos)",
      any(r["user_id"] == -100123 for r in acessos.listar(conexao)))

conexao.close()

# --- a tabela é compatível com a do bot -------------------------------------
raiz = pathlib.Path(__file__).resolve().parents[1]
esquema_bot = (raiz / "database.py").read_text(encoding="utf-8")
colunas_bot = [c for c in ("user_id", "label", "is_owner", "granted_at")
               if f"{c}" in esquema_bot.split("CREATE TABLE IF NOT EXISTS access")[1][:400]]
check("as colunas são as mesmas do database.py", len(colunas_bot) == 4, str(colunas_bot))

print()
if falhas:
    print(f"{len(falhas)} verificação(ões) falharam: {', '.join(falhas)}")
    raise SystemExit(1)
print("Tudo bem.")
