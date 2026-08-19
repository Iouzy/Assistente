"""Actualização do programa compilado, pela API de *Releases* do GitHub.

Enquanto o assistente correu a partir do código, actualizar era `git pull`.
O `.exe` não tem repositório nenhum onde puxar: pergunta ao GitHub qual é a
última versão publicada, compara com a sua, e — **só se lhe carregarem no
botão** — descarrega o instalador e corre-o.

A ordem é deliberada e foi pedida assim: *verificar → avisar → esperar*. Nunca
se actualiza sozinho. O painel verifica uma vez no arranque, escreve na
consola se houver novidade, e fica quieto.

Só biblioteca-padrão (`urllib`), como o `acessos.py`: isto tem de funcionar
dentro do `.exe` sem arrastar dependências novas para o pacote.
"""

from __future__ import annotations

import json
import os
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import caminhos
import versao

# O repositório de onde vêm as versões. Configurável para quem tenha um fork
# — sem isto, um fork ficava a receber avisos de versões do repositório
# original, que não são as suas.
REPOSITORIO = os.environ.get("ASSISTENTE_REPOSITORIO", "Iouzy/Assistente").strip()

API_RELEASES = f"https://api.github.com/repos/{REPOSITORIO}/releases/latest"

# O GitHub recusa pedidos sem User-Agent com um 403.
CABECALHOS = {
    "User-Agent": f"Assistente/{versao.VERSAO}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

TEMPO_LIMITE = 15


class ErroActualizacao(RuntimeError):
    """Levantada quando não foi possível verificar ou instalar uma versão."""


# ---------------------------------------------------------------------------
# Comparação de versões
# ---------------------------------------------------------------------------
def analisar(bruto: str) -> tuple[int, ...] | None:
    """Transforma «v1.2.3» em (1, 2, 3). Devolve None se não perceber.

    Devolver None em vez de adivinhar é intencional: uma etiqueta com um
    formato inesperado passaria a comparar mal, e comparar mal aqui significa
    ou nunca avisar de uma versão nova, ou avisar sempre.
    """
    bruto = (bruto or "").strip().lstrip("vV")
    if not bruto:
        return None
    pedacos = bruto.split(".")
    if not 1 <= len(pedacos) <= 4:
        return None
    try:
        return tuple(int(p) for p in pedacos)
    except ValueError:
        return None


def mais_recente(candidata: str, actual: str) -> bool:
    """True se `candidata` for uma versão posterior a `actual`."""
    a = analisar(candidata)
    b = analisar(actual)
    if a is None or b is None:
        return False
    # Compara (1, 2) com (1, 2, 0) sem os considerar diferentes.
    tamanho = max(len(a), len(b))
    a += (0,) * (tamanho - len(a))
    b += (0,) * (tamanho - len(b))
    return a > b


# ---------------------------------------------------------------------------
# Perguntar ao GitHub
# ---------------------------------------------------------------------------
def _pedir(url: str) -> bytes:
    pedido = urllib.request.Request(url, headers=CABECALHOS)
    try:
        with urllib.request.urlopen(pedido, timeout=TEMPO_LIMITE) as resposta:
            return resposta.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise ErroActualizacao(
                f"O repositório {REPOSITORIO} ainda não publicou nenhuma versão."
            ) from exc
        raise ErroActualizacao(f"O GitHub respondeu {exc.code}.") from exc
    except (urllib.error.URLError, ssl.SSLError, TimeoutError) as exc:
        raise ErroActualizacao(f"Não consegui contactar o GitHub: {exc}") from exc


def ultima_versao() -> dict[str, str]:
    """A última versão publicada: `versao`, `notas` e `url` do instalador.

    O `url` pode vir vazio se a *release* existir mas ainda não tiver o
    instalador anexado (compilação a meio, por exemplo) — quem chama tem de
    contar com isso antes de propor a instalação.
    """
    try:
        dados = json.loads(_pedir(API_RELEASES))
    except (ValueError, TypeError) as exc:
        raise ErroActualizacao("O GitHub devolveu uma resposta que não percebi.") from exc

    etiqueta = str(dados.get("tag_name") or "").strip()
    if not etiqueta:
        raise ErroActualizacao("A última versão publicada não tem etiqueta.")

    url = ""
    for anexo in dados.get("assets") or []:
        nome = str(anexo.get("name") or "")
        # O instalador, não o `.exe` solto nem o `.zip`: é ele que sabe parar
        # o programa a correr, substituir os ficheiros e refazer os atalhos.
        if nome.lower().endswith(".exe") and "instalador" in nome.lower():
            url = str(anexo.get("browser_download_url") or "")
            break

    return {
        "versao": etiqueta.lstrip("vV"),
        "notas": str(dados.get("body") or "").strip(),
        "url": url,
    }


def ha_versao_nova() -> dict[str, str] | None:
    """A versão nova, se houver. `None` quando já está actualizado.

    Levanta `ErroActualizacao` se não conseguir perguntar — quem chama decide
    se isso merece uma linha na consola ou silêncio.
    """
    ultima = ultima_versao()
    return ultima if mais_recente(ultima["versao"], versao.VERSAO) else None


# ---------------------------------------------------------------------------
# Descarregar e instalar
# ---------------------------------------------------------------------------
def descarregar(url: str, destino: Path | None = None) -> Path:
    """Traz o instalador para o disco e devolve o caminho do ficheiro."""
    if not url:
        raise ErroActualizacao(
            "A versão nova ainda não tem instalador publicado. Tente daqui a nada."
        )
    if not url.startswith("https://"):
        # O instalador é executado a seguir: por http seria um convite a que
        # alguém no caminho decidisse o que corre nesta máquina.
        raise ErroActualizacao("O endereço do instalador não é https — recusado.")

    if destino is None:
        pasta = Path(tempfile.mkdtemp(prefix="assistente-actualizacao-"))
        destino = pasta / "Assistente-instalador.exe"

    pedido = urllib.request.Request(url, headers=CABECALHOS)
    try:
        with urllib.request.urlopen(pedido, timeout=TEMPO_LIMITE * 4) as resposta:
            with open(destino, "wb") as ficheiro:
                while pedaco := resposta.read(64 * 1024):
                    ficheiro.write(pedaco)
    except (urllib.error.URLError, ssl.SSLError, TimeoutError, OSError) as exc:
        raise ErroActualizacao(f"Falhou a descarga do instalador: {exc}") from exc

    return destino


def instalar(instalador: Path) -> None:
    """Lança o instalador e devolve o controlo — quem chama tem de sair.

    O instalador do Inno Setup fecha o programa a correr, substitui os
    ficheiros e volta a abrir o painel no fim. Por isso o painel não pode
    ficar à espera dele: **tem de terminar já a seguir a esta chamada**, ou é
    ele próprio que impede a substituição dos seus ficheiros.

    Os dados não estão em risco: vivem em `%LOCALAPPDATA%\\Assistente`, que o
    instalador nunca toca (ver `caminhos.py`).
    """
    if sys.platform != "win32":
        raise ErroActualizacao(
            "A actualização automática é só para o programa compilado em Windows. "
            "Em Linux, actualize com `git pull` na pasta do projecto."
        )

    try:
        subprocess.Popen(
            [
                str(instalador),
                "/SILENT",            # sem assistente, mas com barra de progresso
                "/CLOSEAPPLICATIONS",  # fecha o painel e o bot antes de substituir
                "/RESTARTAPPLICATIONS",
                "/NORESTART",         # nunca reiniciar o computador
            ],
            close_fds=True,
        )
    except OSError as exc:
        raise ErroActualizacao(f"Não consegui lançar o instalador: {exc}") from exc


def actualizar() -> str:
    """Verifica, descarrega e lança o instalador. Devolve a versão a instalar.

    É o que o botão «Actualizar agora» faz no programa compilado. Só depois de
    isto devolver é que o painel se fecha.
    """
    if not caminhos.CONGELADO:
        raise ErroActualizacao(
            "Isto é a actualização do programa compilado. A correr a partir do "
            "código, use o `git pull` (é o que o botão faz nesse caso)."
        )

    nova = ha_versao_nova()
    if nova is None:
        return ""

    instalar(descarregar(nova["url"]))
    return nova["versao"]
