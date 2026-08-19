"""Onde vive o código e onde vivem os dados — a única fonte para os dois.

Há duas formas de correr o assistente e elas não guardam os ficheiros no mesmo
sítio:

* **A partir do código** (`python main.py`, `.venv`, o repositório clonado): a
  base de dados, o `.env` e o registo ficam na própria pasta do projecto, como
  sempre ficaram. Nada muda para quem já o usa assim.
* **Compilado** (`Assistente.exe`, PyInstaller): o programa é instalado em
  `C:\\Program Files` e **é substituído inteiro a cada actualização**. Guardar
  lá a base de dados era perdê-la na primeira versão nova. Por isso os dados
  passam para `%LOCALAPPDATA%\\Assistente`, que o instalador nunca toca.

Só biblioteca-padrão, pela mesma razão que o `acessos.py`: isto é importado
pelo painel antes de existir ambiente virtual nenhum, e importado pelo
`config.py`, que não pode depender de nada que ainda não esteja instalado.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# `sys.frozen` é posto pelo PyInstaller no executável compilado. É a única
# maneira fiável de o programa saber se está dentro de um `.exe` ou a correr
# como um conjunto de ficheiros `.py`.
CONGELADO = bool(getattr(sys, "frozen", False))

if CONGELADO:
    # `_MEIPASS` é a pasta temporária onde o PyInstaller extrai o que
    # empacotou (o `.env.example`, por exemplo). Some quando o programa
    # termina — nunca lá escrever.
    RAIZ_CODIGO = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    # A pasta onde o `.exe` está instalado, essa persiste — é onde se procura
    # um `.env` deixado por uma instalação anterior.
    PASTA_PROGRAMA = Path(sys.executable).resolve().parent
else:
    RAIZ_CODIGO = Path(__file__).resolve().parent
    PASTA_PROGRAMA = RAIZ_CODIGO


def _pasta_dados_predefinida() -> Path:
    """A pasta de dados quando ninguém a escolhe explicitamente."""
    if not CONGELADO:
        # Modo código: tudo na pasta do projecto, como sempre.
        return RAIZ_CODIGO

    if sys.platform == "win32":
        # LOCALAPPDATA e não APPDATA: a base de dados é grande, muda a toda a
        # hora e não faz sentido nenhum andar a ser sincronizada por perfis
        # móveis de domínio.
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
    else:
        base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / "Assistente"


# `ASSISTENTE_DADOS` permite apontar os dados para outro sítio — uma pasta
# sincronizada, um disco cifrado, ou uma cópia isolada para testes.
_escolhida = os.environ.get("ASSISTENTE_DADOS", "").strip()
PASTA_DADOS = Path(_escolhida).expanduser() if _escolhida else _pasta_dados_predefinida()

FICHEIRO_ENV = PASTA_DADOS / ".env"
FICHEIRO_STOP = PASTA_DADOS / ".stop-assistente"
EXEMPLO_ENV = RAIZ_CODIGO / ".env.example"


def garantir_pasta_dados() -> Path:
    """Cria a pasta de dados se faltar e devolve-a.

    Em Windows, uma pasta criada dentro de `%LOCALAPPDATA%` já herda uma ACL
    que só dá acesso ao próprio utilizador. Fora do Windows é preciso dizê-lo:
    lá dentro fica o `.env` com as credenciais e a base de dados com as notas.
    """
    PASTA_DADOS.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        try:
            PASTA_DADOS.chmod(0o700)
        except OSError:
            pass
    return PASTA_DADOS


try:
    # Feito à importação de propósito: o `config.py` lê o `.env` de dentro
    # desta pasta no momento em que é importado, e o `database.py` cria lá a
    # base logo a seguir — os dois falhariam se a pasta ainda não existisse.
    # A `%LOCALAPPDATA%\Assistente` não existe antes da primeira execução.
    garantir_pasta_dados()
except OSError:
    # Sem pasta de dados o programa não vai longe, mas quem levanta o erro
    # com uma mensagem legível é o `config.validate()` — não uma importação.
    pass


def resolver(caminho: str) -> str:
    """Ancora um caminho relativo na pasta de dados; deixa os absolutos em paz.

    É por aqui que passam o `DATABASE_PATH` e o `LOG_FILE` do `.env`: escrever
    lá `assistente.db` significa «na minha pasta de dados», seja ela qual for.
    """
    if not caminho:
        return caminho
    p = Path(caminho).expanduser()
    return str(p if p.is_absolute() else PASTA_DADOS / p)


# ---------------------------------------------------------------------------
# Primeira execução do programa compilado
# ---------------------------------------------------------------------------
def _candidatos_instalacao_antiga() -> list[Path]:
    """Pastas onde pode estar uma instalação anterior, por ordem de aposta.

    Quem já usava o assistente tem-no numa pasta clonada do repositório, com o
    `.env` e o `assistente.db` lá dentro. Sem isto, instalar o `.exe`
    significava escrever outra vez as credenciais e começar com a agenda vazia.
    """
    candidatos = [PASTA_PROGRAMA]

    indicada = os.environ.get("ASSISTENTE_PASTA_ANTIGA", "").strip()
    if indicada:
        candidatos.insert(0, Path(indicada).expanduser())

    casa = Path.home()
    for pai in (casa, casa / "Desktop", casa / "Ambiente de Trabalho", casa / "Documents"):
        candidatos.append(pai / "Assistente")

    return candidatos


def _copiar_base_dados(origem: Path, destino: Path) -> bool:
    """Copia uma base SQLite inteira e coerente. Devolve True se conseguiu."""
    import sqlite3

    try:
        # `mode=ro` porque isto é uma leitura de dados de outrem: abrir em
        # escrita criaria ficheiros `-wal` na instalação antiga.
        with sqlite3.connect(f"file:{origem}?mode=ro", uri=True) as antiga:
            with sqlite3.connect(destino) as nova:
                antiga.backup(nova)
    except (sqlite3.Error, OSError):
        destino.unlink(missing_ok=True)
        return False

    try:
        os.chmod(destino, 0o600)
    except OSError:
        pass
    return True


def importar_dados_antigos() -> list[str]:
    """Traz o `.env` e a base de dados de uma instalação anterior, se houver.

    Só corre quando a pasta de dados ainda não tem `.env` — ou seja, na
    primeira execução. Copia, nunca move: se algo correr mal, a instalação
    antiga continua inteira e utilizável.

    Devolve os nomes dos ficheiros importados (vazio se não havia nada).
    """
    if FICHEIRO_ENV.exists():
        return []

    for pasta in _candidatos_instalacao_antiga():
        origem_env = pasta / ".env"
        try:
            if not origem_env.is_file() or origem_env.resolve() == FICHEIRO_ENV.resolve():
                continue
        except OSError:
            continue

        garantir_pasta_dados()
        importados: list[str] = []
        try:
            shutil.copyfile(origem_env, FICHEIRO_ENV)
            os.chmod(FICHEIRO_ENV, 0o600)
            importados.append(".env")
        except OSError:
            # Sem credenciais não vale a pena trazer o resto: o painel pede-as
            # na aba «Credenciais» e a base de dados seria só confusão.
            return []

        # A base de dados vai a seguir. Trazer as credenciais e deixar a agenda
        # para trás daria um assistente que arranca, responde e não sabe nada
        # do que lhe foi dito até aqui — pior do que não importar de todo.
        #
        # Copiada pela API de backup do SQLite e não com `shutil.copyfile`: a
        # base está em modo WAL, e copiar o `.db` sem o `-wal` que o acompanha
        # deixa de fora tudo o que ainda não foi integrado. O backup resolve
        # isso e funciona mesmo com o bot antigo a correr.
        origem_db = pasta / "assistente.db"
        if origem_db.is_file():
            if _copiar_base_dados(origem_db, PASTA_DADOS / "assistente.db"):
                importados.append("assistente.db")

        return importados

    return []


def preparar() -> list[str]:
    """Deixa a pasta de dados pronta a usar. Chamada uma vez, no arranque."""
    garantir_pasta_dados()
    return importar_dados_antigos()
