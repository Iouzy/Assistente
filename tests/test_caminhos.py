"""Testa onde é que o assistente decide guardar os dados.

Isto separa duas execuções que não podem trocar de sítio: a partir do código
(tudo na pasta do projecto, como sempre foi) e compilada (tudo em
`%LOCALAPPDATA%\\Assistente`, porque a pasta do programa é substituída a cada
actualização).

O modo compilado não se pode testar sem compilar, mas pode-se **simular**: o
`caminhos.py` decide tudo à importação, a partir de `sys.frozen` e de duas
variáveis de ambiente. Basta prepará-las e voltar a importar o módulo.

Não abre janela nenhuma e não fala com a rede: só biblioteca-padrão.
"""
import importlib
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

RAIZ = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

import caminhos  # noqa: E402

falhas = []


def check(nome, condicao, detalhe=""):
    marca = "OK " if condicao else "FALHA"
    print(f"[{marca}] {nome}" + (f" -> {detalhe}" if detalhe else ""))
    if not condicao:
        falhas.append(nome)


def recarregar(**ambiente):
    """Reimporta o `caminhos` com o ambiente indicado (None = apagar).

    O ambiente **fica** posto depois de devolver: a `ASSISTENTE_PASTA_ANTIGA`
    é lida no momento em que se procura a instalação anterior, não à
    importação, e repô-la aqui fazia a procura correr sem ela.
    """
    for chave, valor in ambiente.items():
        if valor is None:
            os.environ.pop(chave, None)
        else:
            os.environ[chave] = str(valor)
    return importlib.reload(caminhos)


# --- a partir do código: nada muda de sítio ---------------------------------
c = recarregar(ASSISTENTE_DADOS=None)
check("a partir do código, não está congelado", c.CONGELADO is False)
check("a partir do código, os dados ficam na pasta do projecto",
      c.PASTA_DADOS == RAIZ, str(c.PASTA_DADOS))
check("o .env é o da pasta do projecto", c.FICHEIRO_ENV == RAIZ / ".env")
check("o ficheiro de paragem é o de sempre",
      c.FICHEIRO_STOP == RAIZ / ".stop-assistente")

# Este é o ponto que faz a diferença entre o painel e o bot falarem do mesmo
# ficheiro ou de dois ficheiros diferentes com o mesmo nome.
check("um caminho relativo é ancorado na pasta de dados",
      c.resolver("assistente.db") == str(RAIZ / "assistente.db"))
absoluto = str(pathlib.Path(tempfile.gettempdir()) / "outra" / "base.db")
check("um caminho absoluto é deixado em paz", c.resolver(absoluto) == absoluto)
check("um caminho vazio continua vazio", c.resolver("") == "")

# --- pasta escolhida à mão --------------------------------------------------
escolhida = pathlib.Path(tempfile.mkdtemp()) / "dados-à-parte"
c = recarregar(ASSISTENTE_DADOS=escolhida)
check("ASSISTENTE_DADOS manda em tudo", c.PASTA_DADOS == escolhida, str(c.PASTA_DADOS))
check("e a pasta é criada logo à importação", escolhida.is_dir())
check("o .env acompanha a pasta escolhida", c.FICHEIRO_ENV == escolhida / ".env")
check("o código continua a ser lido de onde está", c.RAIZ_CODIGO == RAIZ)

# --- importação de uma instalação anterior ----------------------------------
# O caso que isto protege: alguém que já usava o assistente clonado instala o
# programa compilado. Sem importação, ficava com as credenciais por escrever e
# a agenda vazia — e sem perceber porquê, já que os ficheiros antigos estão
# todos lá, noutra pasta.
antiga = pathlib.Path(tempfile.mkdtemp()) / "Assistente"
antiga.mkdir()
(antiga / ".env").write_text(
    "TELEGRAM_TOKEN=123:ANTIGO\nDEEPSEEK_API_KEY=sk-antiga\n", encoding="utf-8"
)
with sqlite3.connect(antiga / "assistente.db") as ligacao:
    ligacao.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, texto TEXT)")
    ligacao.execute("INSERT INTO notes (texto) VALUES ('o código do alarme é 4821')")

nova = pathlib.Path(tempfile.mkdtemp()) / "Assistente"
c = recarregar(ASSISTENTE_DADOS=nova, ASSISTENTE_PASTA_ANTIGA=antiga)
importados = c.importar_dados_antigos()

check("importa o .env da instalação anterior", ".env" in importados, str(importados))
check("com as credenciais lá dentro",
      "123:ANTIGO" in (nova / ".env").read_text(encoding="utf-8"))
check("importa também a base de dados", "assistente.db" in importados)

if (nova / "assistente.db").exists():
    with sqlite3.connect(nova / "assistente.db") as ligacao:
        linhas = ligacao.execute("SELECT texto FROM notes").fetchall()
    check("com as notas lá dentro", linhas == [("o código do alarme é 4821",)], str(linhas))
else:
    check("com as notas lá dentro", False, "a base não chegou a ser copiada")

# Copiar, nunca mover: se a importação corresse mal, a instalação antiga tinha
# de continuar utilizável.
check("a instalação antiga fica intacta",
      (antiga / ".env").exists() and (antiga / "assistente.db").exists())

# --- a importação não se repete ---------------------------------------------
# Sem isto, cada arranque esmagava o `.env` actual pelo antigo — e uma
# credencial trocada no painel voltava atrás sozinha ao reabrir o programa.
(nova / ".env").write_text("TELEGRAM_TOKEN=456:NOVO\n", encoding="utf-8")
c = recarregar(ASSISTENTE_DADOS=nova, ASSISTENTE_PASTA_ANTIGA=antiga)
segunda = c.importar_dados_antigos()
check("com o .env já lá, não volta a importar", segunda == [], str(segunda))
check("e o .env actual não é esmagado",
      "456:NOVO" in (nova / ".env").read_text(encoding="utf-8"))

# --- sem instalação anterior nenhuma ----------------------------------------
vazia = pathlib.Path(tempfile.mkdtemp()) / "sem-nada"
c = recarregar(ASSISTENTE_DADOS=vazia,
               ASSISTENTE_PASTA_ANTIGA=pathlib.Path(tempfile.mkdtemp()) / "nao-existe")
check("sem nada para importar, não inventa", c.importar_dados_antigos() == [])
check("mas a pasta de dados fica criada à mesma", c.garantir_pasta_dados().is_dir())

# Deixa o módulo como estava, para não afectar quem o importe a seguir.
recarregar(ASSISTENTE_DADOS=None, ASSISTENTE_PASTA_ANTIGA=None)

print()
if falhas:
    print(f"❌ {len(falhas)} teste(s) falharam: {falhas}")
    raise SystemExit(1)
print("✅ Todos os testes dos caminhos passaram.")
