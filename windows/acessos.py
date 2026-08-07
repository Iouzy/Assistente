"""Gestão da lista de acesso — a parte que não é interface.

O painel de controlo usa isto para atribuir e retirar permissões sem ser
preciso passar pelo Telegram (`/allow`, `/revoke`).

Só usa a biblioteca-padrão, de propósito: o painel abre mesmo que o ambiente
virtual não esteja criado, e **não** importa o `config.py` — se o fizesse,
carregava o `.env` para o ambiente do próprio painel e o bot arrancado a
seguir herdava essas variáveis, ficando surdo a alterações feitas ao ficheiro
depois de o painel abrir.

A base de dados é a mesma do bot (SQLite em modo WAL), pelo que se pode
escrever com o assistente a correr. O bot relê a lista de poucos em poucos
segundos — ver `watch_access_list` no `main.py`.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

RAIZ = Path(__file__).resolve().parent.parent
ENV_FILE = RAIZ / ".env"

# Mesma definição que está no `database.py`; repetida aqui para o painel poder
# preparar a lista antes da primeira vez que o bot arranca.
DDL_ACESSO = """
CREATE TABLE IF NOT EXISTS access (
    user_id    INTEGER PRIMARY KEY,
    label      TEXT    NOT NULL DEFAULT '',
    is_owner   INTEGER NOT NULL DEFAULT 0,
    granted_at TEXT    NOT NULL
);
"""


class ErroAcesso(RuntimeError):
    """Levantada quando uma operação sobre a lista não pode ser feita."""


# ---------------------------------------------------------------------------
# Leitura da configuração
# ---------------------------------------------------------------------------
def ler_env(caminho: Path | str = ENV_FILE) -> dict[str, str]:
    """Lê um ficheiro `.env` para um dicionário. Ficheiro em falta = vazio."""
    valores: dict[str, str] = {}
    try:
        texto = Path(caminho).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return valores

    for linha in texto.splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, _, valor = linha.partition("=")
        chave = chave.strip().removeprefix("export ").strip()
        valor = valor.strip()
        if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "\"'":
            valor = valor[1:-1]
        else:
            # Comentário no fim da linha (só vale fora de aspas).
            valor = valor.split(" #", 1)[0].strip()
        if chave:
            valores[chave] = valor
    return valores


def ler_ids(bruto: str) -> list[int]:
    """Interpreta uma lista de ids separados por vírgulas, ignorando o lixo."""
    ids: list[int] = []
    for pedaco in bruto.replace(";", ",").split(","):
        pedaco = pedaco.strip()
        if not pedaco:
            continue
        try:
            ids.append(int(pedaco))
        except ValueError:
            continue
    return ids


def caminho_base_dados(env: dict[str, str] | None = None) -> Path:
    """Onde está o `assistente.db`, seguindo as mesmas regras do `config.py`."""
    env = ler_env() if env is None else env
    # O ambiente do sistema ganha ao ficheiro (`load_dotenv(override=False)`).
    bruto = os.environ.get("DATABASE_PATH") or env.get("DATABASE_PATH") or "assistente.db"
    caminho = Path(bruto.strip())
    # O bot corre com a raiz do projeto como pasta de trabalho.
    return caminho if caminho.is_absolute() else RAIZ / caminho


def lista_fixa(env: dict[str, str] | None = None) -> tuple[list[int], str]:
    """Ids fixados em `ALLOWED_USER_IDS`, e de onde vêm.

    Enquanto essa variável estiver preenchida, o bot ignora a base de dados —
    é preciso dizê-lo a quem estiver a olhar para o painel.

    Devolve `([], "")` quando a variável está vazia (o caso normal).
    """
    env = ler_env() if env is None else env
    do_sistema = os.environ.get("ALLOWED_USER_IDS", "").strip()
    if do_sistema:
        return ler_ids(do_sistema), "sistema"
    do_ficheiro = env.get("ALLOWED_USER_IDS", "").strip()
    if do_ficheiro:
        return ler_ids(do_ficheiro), "ficheiro .env"
    return [], ""


def esvaziar_lista_fixa(caminho: Path | str = ENV_FILE) -> list[int]:
    """Esvazia a linha `ALLOWED_USER_IDS=` do `.env` e devolve os ids que lá estavam.

    É o passo que entrega a gestão do acesso ao painel. Guarda uma cópia do
    ficheiro original em `.env.bak` antes de escrever.
    """
    caminho = Path(caminho)
    if not caminho.exists():
        raise ErroAcesso(f"Não encontrei o ficheiro {caminho}.")

    # `newline=""` para não converter os fins de linha do Windows.
    with open(caminho, encoding="utf-8", newline="") as ficheiro:
        linhas = ficheiro.readlines()

    ids: list[int] = []
    novas: list[str] = []
    encontrada = False
    for linha in linhas:
        chave, sep, valor = linha.partition("=")
        nome = chave.strip().removeprefix("export ").strip()
        if sep and nome == "ALLOWED_USER_IDS":
            ids += ler_ids(valor)
            fim = "\r\n" if linha.endswith("\r\n") else ("\n" if linha.endswith("\n") else "")
            novas.append(f"ALLOWED_USER_IDS={fim}")
            encontrada = True
        else:
            novas.append(linha)

    if not encontrada:
        return []

    # A cópia é o ficheiro .env inteiro — token do Telegram e chave da API
    # incluídos. Criada de raiz a 0600, e não copiada e apertada a seguir: no
    # meio das duas coisas havia uma janela em que ficava legível por todos.
    backup = caminho.with_name(caminho.name + ".bak")
    try:
        original = caminho.read_bytes()
        with open(os.open(backup, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600), "wb") as copia:
            copia.write(original)
        try:
            os.chmod(backup, 0o600)  # se já existia, o modo antigo mantinha-se
        except OSError:
            pass
        with open(caminho, "w", encoding="utf-8", newline="") as ficheiro:
            ficheiro.writelines(novas)
    except OSError as exc:
        raise ErroAcesso(f"Não consegui escrever no {caminho.name}: {exc}") from exc
    return ids


# ---------------------------------------------------------------------------
# Base de dados
# ---------------------------------------------------------------------------
def ligar(caminho: Path | str | None = None) -> sqlite3.Connection:
    """Abre a base de dados do bot, criando a tabela do acesso se faltar.

    Criar a tabela permite dar permissões *antes* da primeira execução — de
    outra forma o painel só serviria depois de o bot já ter arrancado uma vez.
    """
    caminho = caminho_base_dados() if caminho is None else Path(caminho)
    try:
        # Mesma protecção que o bot aplica: se for o painel a criar o ficheiro
        # primeiro, ele não pode nascer legível por toda a máquina.
        if not caminho.exists():
            caminho.parent.mkdir(parents=True, exist_ok=True)
            os.close(os.open(caminho, os.O_CREAT | os.O_WRONLY, 0o600))
        conexao = sqlite3.connect(str(caminho), timeout=10.0)
        conexao.row_factory = sqlite3.Row
        conexao.execute("PRAGMA journal_mode=WAL")
        conexao.executescript(DDL_ACESSO)
        conexao.commit()
    except sqlite3.Error as exc:
        raise ErroAcesso(f"Não consegui abrir {caminho}: {exc}") from exc
    return conexao


def listar(conexao: sqlite3.Connection) -> list[dict[str, Any]]:
    """Quem tem acesso, o dono primeiro."""
    cur = conexao.execute(
        "SELECT user_id, label, is_owner, granted_at FROM access "
        "ORDER BY is_owner DESC, granted_at ASC"
    )
    return [{chave: linha[chave] for chave in linha.keys()} for linha in cur.fetchall()]


def adicionar(
    conexao: sqlite3.Connection,
    user_id: int,
    etiqueta: str = "",
    dono: bool | None = None,
) -> bool:
    """Dá acesso a alguém. Devolve True se ficou registado como dono.

    Com `dono=None` (o normal) a primeira pessoa da lista fica dona, à imagem
    do que o bot faz quando é a primeira a escrever-lhe.
    """
    if dono is None:
        dono = not listar(conexao)

    with conexao:  # commit/rollback automáticos
        conexao.execute(
            """
            INSERT INTO access (user_id, label, is_owner, granted_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (user_id) DO UPDATE SET label = excluded.label
            """,
            (user_id, etiqueta.strip(), 1 if dono else 0, datetime.now().astimezone().isoformat()),
        )
    return bool(dono)


def remover(conexao: sqlite3.Connection, user_id: int) -> bool:
    """Retira o acesso. O dono não pode ser retirado — devolve False.

    Marca também os lembretes por disparar dessa pessoa como disparados, tal
    como o `/revoke` do Telegram faz. O bot já não os entregaria (confirma a
    lista de acesso antes de enviar), mas sem isto ficavam para sempre a dizer
    «por disparar» na base de dados e os jobs mortos acumulavam-se no
    scheduler até ao reinício seguinte.

    A tabela `reminders` pode ainda não existir se o bot nunca tiver arrancado;
    nesse caso não há nada para limpar.
    """
    with conexao:
        cur = conexao.execute(
            "DELETE FROM access WHERE user_id = ? AND is_owner = 0", (user_id,)
        )
        if cur.rowcount == 0:
            return False
        try:
            conexao.execute(
                "UPDATE reminders SET fired = 1 WHERE user_id = ? AND fired = 0",
                (user_id,),
            )
        except sqlite3.OperationalError:
            pass  # base de dados ainda sem a tabela dos lembretes
    return True


def definir_dono(conexao: sqlite3.Connection, user_id: int) -> bool:
    """Passa a coroa a outra pessoa da lista. False se ela não estiver lá."""
    with conexao:
        cur = conexao.execute("SELECT 1 FROM access WHERE user_id = ?", (user_id,))
        if cur.fetchone() is None:
            return False
        conexao.execute("UPDATE access SET is_owner = 0 WHERE is_owner = 1")
        conexao.execute("UPDATE access SET is_owner = 1 WHERE user_id = ?", (user_id,))
    return True


def importar(conexao: sqlite3.Connection, ids: Iterable[int]) -> int:
    """Traz uma lista de ids para a base de dados; o primeiro fica dono."""
    total = 0
    for user_id in ids:
        adicionar(conexao, user_id, "")
        total += 1
    return total
