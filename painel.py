"""Painel de controlo do assistente — versão web local (NiceGUI), para
Windows e Linux por igual.

Uma página servida em localhost: ligar, desligar, consola ao vivo, gestão de
utilizadores, credenciais e actualização. No Windows abre como janela própria
(WebView2, sem moldura de navegador); no Linux, por omissão, numa aba do
navegador — ver `MODO_NATIVO`, mais abaixo. O mesmo ficheiro corre nos dois
sistemas — só o caminho do Python do `.venv`, a forma de abrir a pasta e o
modo nativo mudam consoante `sys.platform`.

Corre de duas maneiras, e sabe distingui-las (`caminhos.CONGELADO`):

* **compilado**, dentro do `Assistente.exe` — arranca o bot relançando o
  próprio executável com `--bot`, e actualiza-se descarregando o instalador
  publicado em *Releases*;
* **a partir do código**, com o `.venv` — arranca `main.py` e actualiza-se
  com `git pull`, como sempre fez.

Substitui o antigo painel em Tkinter (`windows/painel.pyw`): manter dois
painéis com o mesmo conjunto de funcionalidades, um por sistema operativo,
era o dobro do trabalho para metade das funcionalidades — o de Tkinter não
tinha credenciais nem actualização automática.

Pensado para uma pessoa e uma janela de cada vez. Abrir duas abas ao mesmo
tempo funciona, mas a consola e os campos só reflectem a última aba que os
desenhou.

Uso:  duplo clique no atalho (windows/painel.vbs, ou o atalho que o
      linux/instalar.sh regista no menu de aplicações)
      ou  .venv/Scripts/python.exe painel.py   (Windows)
      ou  .venv/bin/python painel.py           (Linux)
"""

from __future__ import annotations

import asyncio
import os
import queue
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import acessos  # noqa: E402
import actualizacao  # noqa: E402
import caminhos  # noqa: E402
import versao  # noqa: E402

from nicegui import app, ui  # noqa: E402

RAIZ = caminhos.RAIZ_CODIGO
PASTA_DADOS = caminhos.PASTA_DADOS
STOP_FILE = caminhos.FICHEIRO_STOP
LOG_FILE = PASTA_DADOS / "assistente.log"

# Compilado (`Assistente.exe`) ou a partir do código (`.venv` + `main.py`)?
# Muda como se arranca o bot e como se actualiza — o resto do painel é igual.
CONGELADO = caminhos.CONGELADO

EM_WINDOWS = sys.platform == "win32"
if EM_WINDOWS:
    PYTHON = RAIZ / ".venv" / "Scripts" / "python.exe"
    # Sem isto, cada `git`/`pip`/`main.py` lançado a partir do painel (que
    # corre sem consola própria, via pythonw.exe) abria a sua própria janela
    # preta por instantes — um efeito lateral só do Windows.
    CREATIONFLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)
else:
    PYTHON = RAIZ / ".venv" / "bin" / "python"
    CREATIONFLAGS = 0


# Pasta de trabalho dos processos que o painel lança. Compilado, a `RAIZ` é
# uma pasta temporária que o PyInstaller extrai e apaga no fim — não serve de
# pasta de trabalho a nada; os dados servem.
PASTA_TRABALHO = PASTA_DADOS if caminhos.CONGELADO else RAIZ


def comando_do_bot() -> list[str]:
    """Como se arranca o processo do bot, neste modo de execução.

    Compilado, o executável relança-se a si próprio: não há `.venv` nem
    `main.py` no disco, só o `Assistente.exe` — ver `assistente.py`.
    """
    if CONGELADO:
        return [sys.executable, "--bot"]
    return [str(PYTHON), "main.py"]


def _tem_pywebview() -> bool:
    try:
        import webview  # noqa: F401
        return True
    except ImportError:
        return False


# Janela própria (via pywebview/WebView2) em vez de uma aba do navegador.
# Por omissão, ligado no Windows — onde o WebView2 já vem instalado com o
# Edge, sem nada a acrescentar — e desligado no Linux, onde pywebview
# depende de bibliotecas de sistema (GTK ou Qt) que podem não estar
# presentes. Pode forçar-se com PAINEL_NATIVE=0 ou =1.
MODO_NATIVO = (
    os.environ.get("PAINEL_NATIVE", "1" if EM_WINDOWS else "0") == "1"
    and _tem_pywebview()
)


def abrir_pasta() -> None:
    """Abre a pasta de dados no gestor de ficheiros do sistema.

    A de dados e não a do programa: é lá que estão a base de dados, o `.env` e
    o registo — o que alguém pode querer copiar, salvaguardar ou apagar.
    """
    caminhos.garantir_pasta_dados()
    try:
        if EM_WINDOWS:
            os.startfile(PASTA_DADOS)  # noqa: S606
        else:
            subprocess.Popen(["xdg-open", str(PASTA_DADOS)])
    except OSError as exc:
        painel._linha(f"Não consegui abrir a pasta: {exc}\n", "erro")

# Porta do painel. Só em localhost — nunca é exposta à rede (ver `ui.run` no
# fim do ficheiro, sem `host="0.0.0.0"`).
PORTA = int(os.environ.get("PAINEL_PORT", "8765"))

# Segundos à espera de um encerramento ordenado antes de terminar à força.
ESPERA_PARAGEM = 30
MAX_LINHAS_CONSOLA = 2000


class Painel:
    """Estado partilhado do painel: o processo do bot e a consola.

    Separado da interface de propósito — os métodos daqui não tocam em
    widgets do NiceGUI directamente, só na fila `linhas`, para poderem ser
    chamados de threads de fundo (leitura do processo, git, pip).
    """

    def __init__(self) -> None:
        self.processo: subprocess.Popen | None = None
        self.a_parar = False
        self.linhas: queue.Queue[tuple[str, str]] = queue.Queue()
        self.log_widget: ui.log | None = None
        self.estado_widget: ui.label | None = None
        self.btn_ligar: ui.button | None = None
        self.btn_parar: ui.button | None = None
        self.btn_actualizar: ui.button | None = None

    def a_correr(self) -> bool:
        return self.processo is not None and self.processo.poll() is None

    # -- controlo do processo -----------------------------------------------
    def ligar(self) -> None:
        if self.a_correr():
            return
        if not CONGELADO and not PYTHON.exists():
            self._linha(f"Não encontrei o Python em: {PYTHON}\n", "erro")
            self._linha(
                "Crie o ambiente virtual com:  "
                + ("python -m venv .venv" if EM_WINDOWS else "python3 -m venv .venv") + "\n",
                "erro",
            )
            return
        if not caminhos.FICHEIRO_ENV.exists():
            self._linha(
                "Falta o ficheiro .env. Preencha as credenciais na aba "
                "«Credenciais» antes de ligar.\n",
                "erro",
            )
            return

        STOP_FILE.unlink(missing_ok=True)

        ambiente = os.environ.copy()
        ambiente["PYTHONUNBUFFERED"] = "1"
        ambiente["PYTHONIOENCODING"] = "utf-8"
        ambiente.setdefault("LOG_FILE", str(LOG_FILE))

        try:
            self.processo = subprocess.Popen(
                comando_do_bot(),
                cwd=str(PASTA_TRABALHO),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=ambiente,
                creationflags=CREATIONFLAGS,
            )
        except OSError as exc:
            self._linha(f"Falha ao arrancar: {exc}\n", "erro")
            return

        self.a_parar = False
        self._marcar(True)
        self._linha("\n▶  A arrancar o assistente...\n", "ok")
        threading.Thread(target=self._ler_saida, daemon=True).start()

    def parar(self) -> None:
        if not self.a_correr():
            return
        self.a_parar = True
        self._marcar(True, "⬤ A encerrar...")
        self._linha("\n■  A pedir encerramento ordenado (a memória é gravada)...\n", "aviso")

        try:
            STOP_FILE.write_text("stop", encoding="utf-8")
        except OSError as exc:
            self._linha(f"Não consegui criar o ficheiro de paragem: {exc}\n", "erro")
            return

        threading.Thread(target=self._esperar_paragem, daemon=True).start()

    def _esperar_paragem(self) -> None:
        for _ in range(ESPERA_PARAGEM):
            if not self.a_correr():
                return  # o _ler_saida trata do resto
            threading.Event().wait(1)
        if self.a_correr():
            self._linha("Não encerrou a tempo — a terminar à força.\n", "erro")
            STOP_FILE.unlink(missing_ok=True)
            if self.processo:
                self.processo.terminate()

    # -- actualização ---------------------------------------------------------
    def procurar_versao_nova(self) -> None:
        """Pergunta se há versão nova e **escreve na consola**. Não instala nada.

        Corre uma vez, no arranque do painel. É a parte «avisar» da regra:
        quem decide se e quando actualiza é quem carrega no botão. Só faz
        sentido no programa compilado — a partir do código, quem manda é o
        `git`, e perguntar ao GitHub por versões publicadas não diria nada
        sobre o que está no repositório local.
        """
        if not CONGELADO:
            return
        try:
            nova = actualizacao.ha_versao_nova()
        except actualizacao.ErroActualizacao as exc:
            # Não conseguir verificar não é um erro que valha a pena gritar:
            # o computador pode simplesmente estar sem rede.
            self._linha(f"(não consegui verificar se há versão nova: {exc})\n")
            return

        if nova is None:
            return

        self._linha(
            f"\n🔔 Há uma versão nova: {nova['versao']} (tem a {versao.VERSAO}).\n"
            "   Carregue em «Actualizar agora» quando lhe der jeito.\n",
            "aviso",
        )
        if nova["notas"]:
            for linha in nova["notas"].splitlines()[:10]:
                self._linha(f"   {linha}\n")

    def actualizar(self) -> None:
        """O botão «Actualizar agora»: instalador no `.exe`, `git pull` no código."""
        if self.a_correr():
            self._linha("Pare o assistente antes de actualizar.\n", "aviso")
            return
        if CONGELADO:
            self._actualizar_compilado()
        else:
            self._actualizar_do_git()

    def _actualizar_compilado(self) -> None:
        """Descarrega o instalador da versão nova e entrega-lhe o controlo."""
        self._linha("\n⟳  A perguntar ao GitHub se há versão nova...\n")
        try:
            nova = actualizacao.ha_versao_nova()
            if nova is None:
                self._linha(f"Já está na última versão ({versao.VERSAO}).\n", "ok")
                return

            self._linha(f"A descarregar a versão {nova['versao']}...\n")
            instalador = actualizacao.descarregar(nova["url"])
            self._linha(
                "Descarregado. O instalador vai fechar o painel e abri-lo outra "
                "vez no fim — os seus dados não são tocados.\n",
                "ok",
            )
            actualizacao.instalar(instalador)
        except actualizacao.ErroActualizacao as exc:
            self._linha(f"{exc}\n", "erro")
            return

        # O instalador não consegue substituir ficheiros que este processo
        # tenha abertos: sair é parte da actualização, não uma consequência.
        app.shutdown()

    def _actualizar_do_git(self) -> None:
        """Traz o código novo, instala dependências se precisar."""
        try:
            antes = self._git("rev-parse", "HEAD")
            self._linha("\n⟳  git pull...\n")
            if not self._executar(["git", "pull"]):
                self._linha("O git devolveu erro — veja acima.\n", "erro")
                return

            depois = self._git("rev-parse", "HEAD")
            if not antes or antes == depois:
                self._linha("Já estava actualizado.\n")
                return

            alterados = self._git("diff", "--name-only", antes, depois).splitlines()
            self._linha(f"{len(alterados)} ficheiro(s) actualizado(s).\n", "ok")

            if "requirements.txt" in alterados:
                self._linha("\nAs dependências do bot mudaram — a instalar...\n")
                self._executar([str(PYTHON), "-m", "pip", "install", "-r", "requirements.txt"])
            if "requirements-painel.txt" in alterados:
                self._linha("\nAs dependências do painel mudaram — a instalar...\n")
                self._executar([str(PYTHON), "-m", "pip", "install", "-r", "requirements-painel.txt"])

            if any(f.startswith(("painel.py", "acessos.py")) for f in alterados):
                self._linha(
                    "\nO painel também foi actualizado — a versão nova só vale depois "
                    "de o reabrir. Feche esta janela e volte a abrir o atalho.\n",
                    "aviso",
                )
            else:
                self._linha("Pronto.\n", "ok")
        except Exception as exc:  # noqa: BLE001 — nunca deixar o ciclo automático morrer
            self._linha(f"Falha a actualizar: {exc}\n", "erro")

    def _git(self, *args: str) -> str:
        try:
            resultado = subprocess.run(
                ["git", *args], cwd=str(PASTA_TRABALHO), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=30,
                creationflags=CREATIONFLAGS,
            )
            return resultado.stdout.strip() if resultado.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired):
            return ""

    def _executar(self, comando: list[str]) -> bool:
        try:
            processo = subprocess.Popen(
                comando, cwd=str(PASTA_TRABALHO), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=CREATIONFLAGS,
            )
        except FileNotFoundError:
            self._linha(f"Não encontrei o comando: {comando[0]}\n", "erro")
            return False
        except OSError as exc:
            self._linha(f"Falha ao executar: {exc}\n", "erro")
            return False

        if processo.stdout is not None:
            for linha in processo.stdout:
                self._linha(linha)
        return processo.wait() == 0

    # -- leitura da saída do bot ---------------------------------------------
    def _ler_saida(self) -> None:
        processo = self.processo
        if processo is None or processo.stdout is None:
            return
        for linha in processo.stdout:
            etiqueta = ""
            if "ERROR" in linha or "Traceback" in linha:
                etiqueta = "erro"
            elif "WARNING" in linha:
                etiqueta = "aviso"
            elif "online como" in linha:
                etiqueta = "ok"
            self._linha(linha, etiqueta)
        codigo = processo.wait()
        self._marcar(False)
        fim = "Assistente parado." if self.a_parar else f"Assistente terminou (código {codigo})."
        self._linha(f"\n{fim}\n", "ok" if self.a_parar or codigo == 0 else "erro")
        STOP_FILE.unlink(missing_ok=True)

    # -- ponte para a interface ----------------------------------------------
    def _linha(self, texto: str, etiqueta: str = "") -> None:
        """Enfileira uma linha. Thread-safe — é daqui que a UI vai buscá-las."""
        self.linhas.put((texto, etiqueta))

    def _marcar(self, ligado: bool, texto: str | None = None) -> None:
        self.linhas.put(("\x00ESTADO", "1" if ligado else "0"))
        if texto:
            self.linhas.put(("\x00TEXTO", texto))

    def drenar_para(self, log_widget: ui.log, estado_widget: ui.label,
                     btn_ligar: ui.button, btn_parar: ui.button) -> None:
        """Chamado por um `ui.timer`: passa a fila para os widgets desta aba."""
        try:
            while True:
                linha, etiqueta = self.linhas.get_nowait()
                if linha == "\x00ESTADO":
                    ligado = etiqueta == "1"
                    estado_widget.text = "⬤ A correr" if ligado else "⬤ Desligado"
                    estado_widget.classes(
                        replace="text-lg font-bold " + ("text-positive" if ligado else "text-grey")
                    )
                    btn_ligar.set_enabled(not ligado)
                    btn_parar.set_enabled(ligado)
                    continue
                if linha == "\x00TEXTO":
                    estado_widget.text = etiqueta
                    continue
                classes = {"erro": "text-negative", "aviso": "text-warning", "ok": "text-positive"}.get(etiqueta)
                log_widget.push(linha.rstrip("\n"), classes=classes)
        except queue.Empty:
            pass


painel = Painel()


# ---------------------------------------------------------------------------
# Credenciais
# ---------------------------------------------------------------------------
def _credenciais_configuradas() -> tuple[bool, bool]:
    env = acessos.ler_env()
    token = env.get("TELEGRAM_TOKEN", "").strip()
    chave = env.get("DEEPSEEK_API_KEY", "").strip()
    return (bool(token) and "exemplo" not in token.lower(),
            bool(chave) and "exemplo" not in chave.lower())


def _guardar_credenciais(campo_token: ui.input, campo_chave: ui.input,
                          estado: ui.label) -> None:
    token = campo_token.value.strip()
    chave = campo_chave.value.strip()

    if not token and not chave:
        estado.text = "Escreva pelo menos um valor para guardar."
        estado.classes(replace="text-warning")
        return

    erros = []
    if token:
        if any(c.isspace() for c in token):
            erros.append("O Telegram Token não pode ter espaços nem quebras de linha.")
        elif ":" not in token:
            erros.append("O Telegram Token parece incompleto — falta a parte antes dos «:».")
    if chave and not chave.startswith("sk-"):
        erros.append("A DeepSeek API Key devia começar por «sk-».")

    if erros:
        estado.text = " ".join(erros)
        estado.classes(replace="text-negative")
        return

    valores = {}
    if token:
        valores["TELEGRAM_TOKEN"] = token
    if chave:
        valores["DEEPSEEK_API_KEY"] = chave

    try:
        acessos.definir_variaveis(valores)
    except acessos.ErroAcesso as exc:
        estado.text = str(exc)
        estado.classes(replace="text-negative")
        return

    campo_token.value = ""
    campo_chave.value = ""
    tem_token, tem_chave = _credenciais_configuradas()
    campo_token.props(f'placeholder="{"já configurado — deixe em branco para manter" if tem_token else "1234567890:AAExemploDoBotFather"}"')
    campo_chave.props(f'placeholder="{"já configurada — deixe em branco para manter" if tem_chave else "sk-..."}"')
    estado.text = "✅ Credenciais guardadas."
    estado.classes(replace="text-positive")
    painel._linha("🔑 Credenciais actualizadas pelo painel.\n", "ok")


# ---------------------------------------------------------------------------
# Utilizadores
# ---------------------------------------------------------------------------
def _colunas_utilizadores():
    return [
        {"name": "coroa", "label": "", "field": "coroa", "align": "center"},
        {"name": "user_id", "label": "Id", "field": "user_id", "align": "left"},
        {"name": "label", "label": "Nome", "field": "label", "align": "left"},
        {"name": "granted_at", "label": "Desde", "field": "granted_at", "align": "left"},
    ]


def _linhas_utilizadores(registos: list[dict]) -> list[dict]:
    return [
        {
            "coroa": "👑" if r["is_owner"] else "",
            "user_id": r["user_id"],
            "label": r["label"] or "(sem nome)",
            "granted_at": str(r["granted_at"] or "")[:10],
        }
        for r in registos
    ]


class PainelUtilizadores:
    """Estado da aba «Utilizadores» — uma ligação à base de dados por aba aberta."""

    def __init__(self) -> None:
        self.conexao = acessos.ligar()
        self.registos: list[dict] = []

    def recarregar(self) -> list[dict]:
        self.registos = acessos.listar(self.conexao)
        return self.registos

    def por_id(self, user_id: int) -> dict | None:
        return next((r for r in self.registos if r["user_id"] == user_id), None)


def construir_aba_utilizadores() -> None:
    gestor = PainelUtilizadores()
    fixos, origem_fixos = acessos.lista_fixa()

    if origem_fixos:
        ids = ", ".join(str(i) for i in fixos) or "(nenhum)"
        with ui.card().classes("w-full bg-warning bg-opacity-10"):
            ui.label(
                f"⚠ A lista está fixada em ALLOWED_USER_IDS ({origem_fixos}): {ids}. "
                "Enquanto assim for, o bot ignora as permissões geridas aqui."
            ).classes("text-warning")

    ui.label("Quem pode falar com o assistente").classes("text-lg font-bold")
    tabela = ui.table(
        columns=_colunas_utilizadores(),
        rows=_linhas_utilizadores(gestor.recarregar()),
        row_key="user_id",
        selection="single",
    ).classes("w-full")

    estado = ui.label().classes("text-sm")

    def _dizer(texto: str, cor: str = "") -> None:
        estado.text = texto
        estado.classes(replace=f"text-sm {cor}")

    def _actualizar_estado_inicial() -> None:
        if not gestor.registos:
            _dizer(
                "Ninguém na lista: o bot está aberto e a primeira pessoa que lhe "
                "escrever fica dona. Adicione-se aqui para o fechar já.",
                "text-warning",
            )
        else:
            _dizer(f"{len(gestor.registos)} utilizador(es) com acesso.", "text-positive")

    _actualizar_estado_inicial()

    def recarregar() -> None:
        tabela.rows = _linhas_utilizadores(gestor.recarregar())
        tabela.update()
        _actualizar_estado_inicial()

    def _seleccionado() -> dict | None:
        if not tabela.selected:
            _dizer("Escolha primeiro alguém da lista.", "text-warning")
            return None
        return gestor.por_id(tabela.selected[0]["user_id"])

    with ui.row().classes("items-end gap-2"):
        campo_id = ui.input("Id do Telegram").props("dense")
        campo_nome = ui.input("Nome (opcional)").props("dense")

        def adicionar() -> None:
            bruto = campo_id.value.strip()
            if not bruto:
                _dizer("Escreva o id do Telegram (só números).", "text-warning")
                return
            try:
                user_id = int(bruto)
            except ValueError:
                _dizer(f"«{bruto}» não é um id — o id é um número.", "text-negative")
                return

            nome = campo_nome.value.strip()
            dono = acessos.adicionar(gestor.conexao, user_id, nome)
            campo_id.value = ""
            campo_nome.value = ""
            recarregar()
            extra = " (é o dono, por ser o primeiro da lista)" if dono else ""
            _dizer(f"✅ {user_id} já pode falar com o bot{extra}.", "text-positive")
            painel._linha(f"👥 Acesso dado a {user_id}{f' ({nome})' if nome else ''}.\n", "ok")

        ui.button("➕ Adicionar", on_click=adicionar).props("dense")

    with ui.row().classes("gap-2"):
        def remover() -> None:
            registo = _seleccionado()
            if registo is None:
                return
            if registo["is_owner"]:
                _dizer("O dono não pode ser removido. Passe primeiro a coroa a outra pessoa.", "text-warning")
                return

            def confirmado() -> None:
                acessos.remover(gestor.conexao, registo["user_id"])
                recarregar()
                _dizer(f"✅ {registo['user_id']} já não tem acesso.", "text-positive")
                painel._linha(f"👥 Acesso retirado a {registo['user_id']}.\n", "aviso")
                dialogo.close()

            with ui.dialog() as dialogo, ui.card():
                ui.label(f"Retirar o acesso a {registo['label'] or registo['user_id']}?")
                with ui.row():
                    ui.button("Cancelar", on_click=dialogo.close).props("flat")
                    ui.button("Retirar", on_click=confirmado, color="negative")
            dialogo.open()

        def tornar_dono() -> None:
            registo = _seleccionado()
            if registo is None:
                return
            if registo["is_owner"]:
                _dizer("Essa pessoa já é a dona.", "text-warning")
                return

            def confirmado() -> None:
                acessos.definir_dono(gestor.conexao, registo["user_id"])
                recarregar()
                _dizer(f"👑 {registo['label'] or registo['user_id']} é agora o dono.", "text-positive")
                painel._linha(f"👥 {registo['user_id']} passou a dono.\n", "ok")
                dialogo.close()

            with ui.dialog() as dialogo, ui.card():
                ui.label(
                    f"Tornar {registo['label'] or registo['user_id']} o dono do assistente? "
                    "O dono não pode ser removido da lista — quem o era deixa de ter essa protecção."
                )
                with ui.row():
                    ui.button("Cancelar", on_click=dialogo.close).props("flat")
                    ui.button("Tornar dono", on_click=confirmado, color="primary")
            dialogo.open()

        def libertar_lista_fixa() -> None:
            ids = acessos.esvaziar_lista_fixa()
            acessos.importar(gestor.conexao, ids)
            recarregar()
            _dizer(
                f"✅ {len(ids)} id(s) copiado(s) para a base de dados. "
                "Pare e volte a ligar o assistente para a mudança valer.",
                "text-positive",
            )
            painel._linha(
                "👥 ALLOWED_USER_IDS esvaziado no .env; o acesso passa a ser gerido no "
                "painel (reinicie o assistente).\n",
                "aviso",
            )

        ui.button("🗑 Remover", on_click=remover).props("dense")
        ui.button("👑 Tornar dono", on_click=tornar_dono).props("dense")
        ui.button("⟳ Recarregar", on_click=recarregar).props("dense")
        if origem_fixos and origem_fixos != "sistema":
            ui.button("Passar a gestão para o painel", on_click=libertar_lista_fixa).props("dense flat")

    ui.label(estado.text)
    ui.label(
        "O id de cada pessoa aparece na consola quando ela tenta escrever ao bot, "
        "ou pode ser obtido enviando /who ao assistente (ou uma mensagem ao "
        "@userinfobot). As alterações valem em poucos segundos — não é preciso "
        "reiniciar o bot."
    ).classes("text-xs text-grey")


# ---------------------------------------------------------------------------
# Página principal
# ---------------------------------------------------------------------------
@ui.page("/")
def pagina_principal() -> None:
    ui.dark_mode(True)
    ui.query("body").classes("bg-black")

    with ui.header().classes("items-center justify-between"):
        with ui.row().classes("items-baseline gap-2"):
            ui.label("🤖 Assistente — Painel de Controlo").classes("text-lg font-bold")
            # A versão à vista: sem ela, «carregue em Actualizar» não tem
            # como ser verificado por quem está a olhar para a janela.
            ui.label(f"v{versao.VERSAO}").classes("text-xs text-grey")
        estado_widget = ui.label("⬤ Desligado").classes("text-lg font-bold text-grey")

    with ui.row().classes("w-full px-4 pt-4 gap-2"):
        btn_ligar = ui.button("▶ Ligar", on_click=painel.ligar)
        btn_parar = ui.button("■ Parar", on_click=painel.parar)
        btn_parar.set_enabled(False)
        ui.button("⟳ Actualizar agora",
                   on_click=lambda: threading.Thread(
                       target=painel.actualizar, daemon=True
                   ).start())
        ui.button("👥 Utilizadores", on_click=lambda: tabs.set_value("utilizadores"))
        ui.button("🔑 Credenciais", on_click=lambda: tabs.set_value("credenciais"))
        ui.button("📁 Pasta", on_click=abrir_pasta)
        ui.button("🧹 Limpar consola", on_click=lambda: log_widget.clear())

    with ui.tabs().classes("w-full") as tabs:
        aba_consola = ui.tab("consola", label="Consola")
        aba_utilizadores = ui.tab("utilizadores", label="Utilizadores")
        aba_credenciais = ui.tab("credenciais", label="Credenciais")

    with ui.tab_panels(tabs, value=aba_consola).classes("w-full"):
        with ui.tab_panel(aba_consola):
            log_widget = ui.log(max_lines=MAX_LINHAS_CONSOLA).classes(
                "w-full h-96 bg-grey-10 font-mono text-sm"
            )
            log_widget.push("Painel pronto. Carregue em «Ligar» para arrancar o assistente.")
            if not CONGELADO and not PYTHON.exists():
                comando_criar = (
                    "python -m venv .venv && .venv\\Scripts\\pip install -r requirements.txt"
                    if EM_WINDOWS else
                    "python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
                )
                log_widget.push(
                    f"AVISO: não encontrei o ambiente virtual em {PYTHON}. Crie-o com: {comando_criar}",
                    classes="text-negative",
                )

        with ui.tab_panel(aba_utilizadores):
            construir_aba_utilizadores()

        with ui.tab_panel(aba_credenciais):
            tem_token, tem_chave = _credenciais_configuradas()
            ui.label("Credenciais do assistente").classes("text-lg font-bold")
            ui.label(
                "Ficam gravadas no ficheiro .env deste computador (não são enviadas "
                "para lado nenhum além do Telegram e da DeepSeek, a quem o bot já "
                "fala directamente)."
            ).classes("text-xs text-grey")
            campo_token = ui.input(
                "Telegram Token",
                password=True, password_toggle_button=True,
            ).classes("w-full").props(
                f'placeholder="{"já configurado — deixe em branco para manter" if tem_token else "1234567890:AAExemploDoBotFather"}"'
            )
            ui.label("Dado pelo @BotFather no Telegram.").classes("text-xs text-grey")
            campo_chave = ui.input(
                "DeepSeek API Key",
                password=True, password_toggle_button=True,
            ).classes("w-full").props(
                f'placeholder="{"já configurada — deixe em branco para manter" if tem_chave else "sk-..."}"'
            )
            ui.label("De https://platform.deepseek.com/api_keys").classes("text-xs text-grey")
            estado_credenciais = ui.label()
            ui.button(
                "Guardar credenciais",
                on_click=lambda: _guardar_credenciais(campo_token, campo_chave, estado_credenciais),
            )

    ui.timer(0.2, lambda: painel.drenar_para(log_widget, estado_widget, btn_ligar, btn_parar))


# ---------------------------------------------------------------------------
# Arranque
# ---------------------------------------------------------------------------
async def _verificar_versao_uma_vez() -> None:
    """Uma verificação, no arranque, que só escreve na consola.

    Já houve aqui um ciclo que actualizava sozinho de 6 em 6 horas. Foi
    removido de propósito: actualizar é uma decisão de quem usa o programa,
    não do programa. Isto pergunta, avisa e cala-se.
    """
    # Espera para não competir com o arranque da própria interface — e para a
    # linha do aviso não aparecer antes de a consola existir para a mostrar.
    await asyncio.sleep(20)
    await asyncio.to_thread(painel.procurar_versao_nova)


@app.on_startup
def _ao_arrancar() -> None:
    asyncio.create_task(_verificar_versao_uma_vez())
    # Em modo nativo é o próprio `ui.run(native=True)` que abre a janela —
    # abrir também o navegador seria a mais.
    if not MODO_NATIVO and os.environ.get("PAINEL_ABRIR_NAVEGADOR", "1") == "1":
        threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{PORTA}/")).start()


def main() -> int:
    # `show=False` porque, sem modo nativo, abrimos o navegador nós próprios
    # em `_ao_arrancar`, com um pequeno atraso — assim não se perde a
    # primeira carga da página numa máquina lenta a arrancar o servidor. Em
    # modo nativo `show` não tem efeito: a janela abre sempre sozinha.
    # `reload=False` porque isto não é ambiente de desenvolvimento: recarregar
    # sozinho ao detectar uma alteração de ficheiro (por exemplo, a meio de um
    # `git pull`) partiria a ligação a meio de uma actualização.
    ui.run(
        title="Assistente — Painel de Controlo",
        host="127.0.0.1",
        port=PORTA,
        dark=True,
        show=False,
        reload=False,
        native=MODO_NATIVO,
        window_size=(1100, 780) if MODO_NATIVO else None,
    )
    return 0


if __name__ in {"__main__", "__mp_main__"}:
    raise SystemExit(main())
