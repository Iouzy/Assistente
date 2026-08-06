"""Painel de controlo do assistente — ligar, desligar e ver a consola.

Uma janela simples (tkinter, que vem com o Python) para não ser preciso andar
sempre pela linha de comandos. Extensão `.pyw` para não abrir consola nenhuma.

O bot corre como processo-filho, com a saída canalizada para a caixa de texto.
Ao carregar em Parar, o pedido é feito através do ficheiro-sentinela que o
`main.py` vigia — encerramento ordenado, com a memória gravada. Só se o
processo não obedecer é que é terminado à força.

O botão «Utilizadores» abre a gestão de permissões: escreve directamente na
mesma tabela que os comandos `/allow` e `/revoke` do Telegram usam (ver
`acessos.py`), com o bot ligado ou desligado.

Uso:  duplo clique em windows/painel.vbs
      ou  .venv\\Scripts\\pythonw.exe windows/painel.pyw
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext

sys.path.insert(0, str(Path(__file__).resolve().parent))
import acessos  # noqa: E402  (o caminho tem de ser preparado antes)

RAIZ = Path(__file__).resolve().parent.parent
STOP_FILE = RAIZ / ".stop-assistente"
PYTHON = RAIZ / ".venv" / "Scripts" / "python.exe"
LOG_FILE = RAIZ / "assistente.log"

# Máximo de linhas mantidas na caixa de texto (senão a janela fica pesada).
MAX_LINHAS = 2000
# Segundos à espera de um encerramento ordenado antes de terminar à força.
ESPERA_PARAGEM = 30

CORES = {
    "fundo": "#1e1e2e",
    "texto": "#cdd6f4",
    "ligado": "#a6e3a1",
    "desligado": "#6c7086",
    "erro": "#f38ba8",
    "aviso": "#f9e2af",
    "botao": "#313244",
}


class Painel:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.processo: subprocess.Popen | None = None
        self.linhas: queue.Queue[str] = queue.Queue()
        self.a_parar = False
        self.janela_utilizadores: JanelaUtilizadores | None = None

        root.title("Assistente — Painel de Controlo")
        root.geometry("860x560")
        root.configure(bg=CORES["fundo"])
        root.protocol("WM_DELETE_WINDOW", self.ao_fechar)

        self._construir_barra()
        self._construir_consola()

        self.root.after(100, self._drenar_saida)
        self._escrever("Painel pronto. Carregue em «Ligar» para arrancar o assistente.\n")
        if not PYTHON.exists():
            self._escrever(
                f"AVISO: não encontrei o ambiente virtual em {PYTHON}.\n"
                "Crie-o com:  python -m venv .venv\n",
                "erro",
            )

    # -- interface ---------------------------------------------------------
    def _construir_barra(self) -> None:
        barra = tk.Frame(self.root, bg=CORES["fundo"])
        barra.pack(fill="x", padx=12, pady=(12, 6))

        self.estado = tk.Label(
            barra, text="⬤ Desligado", bg=CORES["fundo"], fg=CORES["desligado"],
            font=("Segoe UI", 11, "bold"),
        )
        self.estado.pack(side="left", padx=(0, 16))

        def botao(texto, comando):
            b = tk.Button(
                barra, text=texto, command=comando, bg=CORES["botao"], fg=CORES["texto"],
                activebackground="#45475a", activeforeground=CORES["texto"],
                relief="flat", padx=14, pady=6, font=("Segoe UI", 10), cursor="hand2",
                borderwidth=0,
            )
            b.pack(side="left", padx=4)
            return b

        self.btn_ligar = botao("▶  Ligar", self.ligar)
        self.btn_parar = botao("■  Parar", self.parar)
        self.btn_parar.config(state="disabled")
        self.btn_actualizar = botao("⟳  Actualizar", self.actualizar)
        botao("👥  Utilizadores", self.abrir_utilizadores)
        botao("📁  Pasta", lambda: os.startfile(RAIZ))  # noqa: S606
        botao("🧹  Limpar", self.limpar_consola)

    def _construir_consola(self) -> None:
        self.consola = scrolledtext.ScrolledText(
            self.root, bg="#11111b", fg=CORES["texto"], insertbackground=CORES["texto"],
            font=("Consolas", 9), relief="flat", wrap="word", padx=10, pady=8,
        )
        self.consola.pack(fill="both", expand=True, padx=12, pady=(6, 12))
        self.consola.tag_config("erro", foreground=CORES["erro"])
        self.consola.tag_config("aviso", foreground=CORES["aviso"])
        self.consola.tag_config("ok", foreground=CORES["ligado"])
        self.consola.config(state="disabled")

    def _escrever(self, texto: str, etiqueta: str = "") -> None:
        self.consola.config(state="normal")
        self.consola.insert("end", texto, etiqueta)

        # Apara o início para a caixa não crescer indefinidamente.
        total = int(self.consola.index("end-1c").split(".")[0])
        if total > MAX_LINHAS:
            self.consola.delete("1.0", f"{total - MAX_LINHAS}.0")

        self.consola.see("end")
        self.consola.config(state="disabled")

    def _marcar(self, ligado: bool, texto: str | None = None) -> None:
        self.estado.config(
            text=texto or ("⬤ A correr" if ligado else "⬤ Desligado"),
            fg=CORES["ligado"] if ligado else CORES["desligado"],
        )
        self.btn_ligar.config(state="disabled" if ligado else "normal")
        self.btn_parar.config(state="normal" if ligado else "disabled")

    def limpar_consola(self) -> None:
        self.consola.config(state="normal")
        self.consola.delete("1.0", "end")
        self.consola.config(state="disabled")

    def abrir_utilizadores(self) -> None:
        """Abre (ou traz para a frente) a janela de permissões."""
        if self.janela_utilizadores is not None and self.janela_utilizadores.winfo_exists():
            self.janela_utilizadores.lift()
            self.janela_utilizadores.focus_force()
            return
        try:
            self.janela_utilizadores = JanelaUtilizadores(self.root, self._escrever)
        except acessos.ErroAcesso as exc:
            messagebox.showerror("Utilizadores", str(exc))
            self.janela_utilizadores = None

    # -- controlo do processo ---------------------------------------------
    def a_correr(self) -> bool:
        return self.processo is not None and self.processo.poll() is None

    def ligar(self) -> None:
        if self.a_correr():
            return
        if not PYTHON.exists():
            messagebox.showerror("Assistente", f"Não encontrei o Python em:\n{PYTHON}")
            return

        STOP_FILE.unlink(missing_ok=True)

        ambiente = os.environ.copy()
        # Saída sem buffer e em UTF-8, senão os acentos e os emojis saem trocados
        # e as mensagens só apareciam aos blocos.
        ambiente["PYTHONUNBUFFERED"] = "1"
        ambiente["PYTHONIOENCODING"] = "utf-8"
        ambiente.setdefault("LOG_FILE", str(LOG_FILE))

        try:
            self.processo = subprocess.Popen(
                [str(PYTHON), "main.py"],
                cwd=str(RAIZ),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=ambiente,
                # Sem janela de consola: a saída vem para esta caixa.
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            self._escrever(f"Falha ao arrancar: {exc}\n", "erro")
            return

        self.a_parar = False
        self._marcar(True)
        self._escrever("\n▶  A arrancar o assistente...\n", "ok")
        threading.Thread(target=self._ler_saida, daemon=True).start()

    def parar(self) -> None:
        if not self.a_correr():
            return
        self.a_parar = True
        self._marcar(True, "⬤ A encerrar...")
        self._escrever("\n■  A pedir encerramento ordenado (a memória é gravada)...\n", "aviso")

        try:
            STOP_FILE.write_text("stop", encoding="utf-8")
        except OSError as exc:
            self._escrever(f"Não consegui criar o ficheiro de paragem: {exc}\n", "erro")

        self.root.after(1000, lambda: self._verificar_paragem(1))

    def _verificar_paragem(self, segundos: int) -> None:
        if not self.a_correr():
            return  # o _ler_saida trata do resto
        if segundos >= ESPERA_PARAGEM:
            self._escrever("Não encerrou a tempo — a terminar à força.\n", "erro")
            STOP_FILE.unlink(missing_ok=True)
            if self.processo:
                self.processo.terminate()
            return
        self.root.after(1000, lambda: self._verificar_paragem(segundos + 1))

    def actualizar(self) -> None:
        """Traz o código novo, instala dependências e reinicia-se se precisar."""
        if self.a_correr():
            messagebox.showinfo("Assistente", "Pare o assistente antes de actualizar.")
            return
        self.btn_actualizar.config(state="disabled")
        threading.Thread(target=self._actualizar_em_fundo, daemon=True).start()

    def _actualizar_em_fundo(self) -> None:
        try:
            antes = self._git("rev-parse", "HEAD")
            self.linhas.put("\n⟳  git pull...\n")
            if not self._executar(["git", "pull"]):
                self.linhas.put("O git devolveu erro — veja acima.\n")
                return

            depois = self._git("rev-parse", "HEAD")
            if not antes or antes == depois:
                self.linhas.put("Já estava actualizado.\n")
                return

            alterados = self._git("diff", "--name-only", antes, depois).splitlines()
            self.linhas.put(f"{len(alterados)} ficheiro(s) actualizado(s).\n")

            # Uma versão nova pode exigir bibliotecas novas — foi assim que o
            # python-telegram-bot antigo partiu o arranque no Python 3.14.
            if "requirements.txt" in alterados:
                self.linhas.put("\nAs dependências mudaram — a instalar...\n")
                self._executar([str(PYTHON), "-m", "pip", "install", "-r", "requirements.txt"])

            # O painel corre a partir do código que carregou no arranque: se ele
            # próprio mudou, é preciso reabri-lo para a alteração valer.
            if any(f.startswith(("windows/painel", "windows/acessos")) for f in alterados):
                self.root.after(0, self._propor_reinicio)
            else:
                self.linhas.put("Pronto. Ligue o assistente outra vez.\n")
        finally:
            self.root.after(0, lambda: self.btn_actualizar.config(state="normal"))

    def _git(self, *args: str) -> str:
        """Corre um comando git e devolve a saída, ou string vazia se falhar."""
        try:
            resultado = subprocess.run(
                ["git", *args], cwd=str(RAIZ), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return resultado.stdout.strip() if resultado.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired):
            return ""

    def _executar(self, comando: list[str]) -> bool:
        """Corre um comando e vai despejando a saída na consola. True se correu bem."""
        try:
            processo = subprocess.Popen(
                comando, cwd=str(RAIZ), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except FileNotFoundError:
            self.linhas.put(f"Não encontrei o comando: {comando[0]}\n")
            return False
        except OSError as exc:
            self.linhas.put(f"Falha ao executar: {exc}\n")
            return False

        if processo.stdout is not None:
            for linha in processo.stdout:
                self.linhas.put(linha)
        return processo.wait() == 0

    def _propor_reinicio(self) -> None:
        """O painel foi actualizado: propõe reabri-lo já com o código novo."""
        self._escrever(
            "\nO painel de controlo também foi actualizado.\n", "aviso"
        )
        if not messagebox.askyesno(
            "Assistente",
            "O painel de controlo foi actualizado.\n\n"
            "A versão nova só vale depois de o reabrir.\n\n"
            "Reabrir agora?",
        ):
            self._escrever("Feche e volte a abrir o painel quando puder.\n", "aviso")
            return

        pythonw = RAIZ / ".venv" / "Scripts" / "pythonw.exe"
        try:
            subprocess.Popen(
                [str(pythonw), str(Path(__file__).resolve())],
                cwd=str(RAIZ),
                creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
            )
        except OSError as exc:
            messagebox.showerror("Assistente", f"Não consegui reabrir o painel:\n{exc}")
            return
        self.root.destroy()

    # -- leitura da saída --------------------------------------------------
    def _ler_saida(self) -> None:
        """Corre numa thread: lê o processo e empurra as linhas para a fila."""
        processo = self.processo
        if processo is None or processo.stdout is None:
            return
        for linha in processo.stdout:
            self.linhas.put(linha)
        processo.wait()
        self.linhas.put(f"\x00FIM{processo.returncode}")

    def _drenar_saida(self) -> None:
        """Corre no Tk: passa as linhas da fila para a caixa de texto."""
        try:
            while True:
                linha = self.linhas.get_nowait()

                if linha.startswith("\x00FIM"):
                    codigo = linha[4:].strip()
                    self._marcar(False)
                    fim = "Assistente parado." if self.a_parar else f"Assistente terminou (código {codigo})."
                    self._escrever(f"\n{fim}\n", "ok" if self.a_parar or codigo == "0" else "erro")
                    STOP_FILE.unlink(missing_ok=True)
                    continue

                etiqueta = ""
                if "ERROR" in linha or "Traceback" in linha:
                    etiqueta = "erro"
                elif "WARNING" in linha:
                    etiqueta = "aviso"
                elif "online como" in linha:
                    etiqueta = "ok"
                self._escrever(linha, etiqueta)
        except queue.Empty:
            pass
        self.root.after(100, self._drenar_saida)

    # -- fecho -------------------------------------------------------------
    def ao_fechar(self) -> None:
        if not self.a_correr():
            self.root.destroy()
            return

        resposta = messagebox.askyesnocancel(
            "Assistente",
            "O assistente está a correr.\n\n"
            "Sim — parar o assistente e fechar o painel\n"
            "Não — deixar o assistente a correr em segundo plano\n"
            "Cancelar — voltar ao painel",
        )
        if resposta is None:
            return
        if resposta:
            self.parar()
            # Dá tempo ao encerramento ordenado antes de fechar a janela.
            self.root.after(ESPERA_PARAGEM * 1000 + 500, self.root.destroy)
            self.root.after(500, self._fechar_quando_parar)
        else:
            self.root.destroy()

    def _fechar_quando_parar(self) -> None:
        if self.a_correr():
            self.root.after(500, self._fechar_quando_parar)
        else:
            self.root.destroy()


class JanelaUtilizadores(tk.Toplevel):
    """Gestão de quem pode falar com o assistente.

    Escreve na tabela `access` da base de dados — a mesma que os comandos
    `/allow` e `/revoke` usam. Pode fazer-se com o bot a correr: ele relê a
    lista de 10 em 10 segundos, não é preciso reiniciá-lo.
    """

    def __init__(self, pai: tk.Misc, registar=None) -> None:
        super().__init__(pai, bg=CORES["fundo"])
        self.registar = registar or (lambda *_: None)
        self.registos: list[dict] = []

        self.conexao = acessos.ligar()  # levanta ErroAcesso se a BD não abrir
        self.fixos, self.origem_fixos = acessos.lista_fixa()

        self.title("Assistente — Utilizadores")
        self.geometry("620x520")
        self.transient(pai)
        self.protocol("WM_DELETE_WINDOW", self.fechar)

        self._construir()
        self.recarregar()

    # -- interface ---------------------------------------------------------
    def _etiqueta(self, pai, texto, cor=None, fonte=("Segoe UI", 9), **kw):
        return tk.Label(
            pai, text=texto, bg=CORES["fundo"], fg=cor or CORES["texto"],
            font=fonte, justify="left", **kw,
        )

    def _botao(self, pai, texto, comando):
        return tk.Button(
            pai, text=texto, command=comando, bg=CORES["botao"], fg=CORES["texto"],
            activebackground="#45475a", activeforeground=CORES["texto"],
            relief="flat", padx=12, pady=5, font=("Segoe UI", 9), cursor="hand2",
            borderwidth=0,
        )

    def _construir(self) -> None:
        if self.origem_fixos:
            self._construir_aviso_lista_fixa()

        self._etiqueta(
            self, "Quem pode falar com o assistente", fonte=("Segoe UI", 11, "bold")
        ).pack(anchor="w", padx=14, pady=(12, 4))

        moldura = tk.Frame(self, bg=CORES["fundo"])
        moldura.pack(fill="both", expand=True, padx=14)
        barra = tk.Scrollbar(moldura, relief="flat", borderwidth=0)
        barra.pack(side="right", fill="y")
        self.lista = tk.Listbox(
            moldura, bg="#11111b", fg=CORES["texto"], font=("Consolas", 10),
            relief="flat", highlightthickness=0, activestyle="none",
            selectbackground="#45475a", selectforeground=CORES["texto"],
            yscrollcommand=barra.set,
        )
        self.lista.pack(side="left", fill="both", expand=True)
        barra.config(command=self.lista.yview)

        accoes = tk.Frame(self, bg=CORES["fundo"])
        accoes.pack(fill="x", padx=14, pady=8)
        self._botao(accoes, "🗑  Remover", self.remover).pack(side="left", padx=(0, 6))
        self._botao(accoes, "👑  Tornar dono", self.tornar_dono).pack(side="left", padx=6)
        self._botao(accoes, "⟳  Actualizar", self.recarregar).pack(side="left", padx=6)

        # -- adicionar --
        adicionar = tk.Frame(self, bg=CORES["fundo"])
        adicionar.pack(fill="x", padx=14, pady=(4, 0))
        self._etiqueta(adicionar, "Id do Telegram").pack(side="left")
        self.campo_id = tk.Entry(
            adicionar, width=14, bg="#11111b", fg=CORES["texto"], relief="flat",
            insertbackground=CORES["texto"], font=("Consolas", 10),
        )
        self.campo_id.pack(side="left", padx=(6, 12), ipady=3)
        self._etiqueta(adicionar, "Nome").pack(side="left")
        self.campo_nome = tk.Entry(
            adicionar, width=18, bg="#11111b", fg=CORES["texto"], relief="flat",
            insertbackground=CORES["texto"], font=("Segoe UI", 10),
        )
        self.campo_nome.pack(side="left", padx=(6, 12), ipady=3)
        self._botao(adicionar, "➕  Adicionar", self.adicionar).pack(side="left")
        self.campo_id.bind("<Return>", lambda _: self.adicionar())
        self.campo_nome.bind("<Return>", lambda _: self.adicionar())

        self.estado = self._etiqueta(self, "", wraplength=580)
        self.estado.pack(anchor="w", padx=14, pady=(8, 0))

        self._etiqueta(
            self,
            "O id de cada pessoa aparece na consola do painel quando ela tenta "
            "escrever ao bot, ou pode ser obtido enviando /who a este assistente "
            "(ou uma mensagem ao @userinfobot).\n"
            "As alterações valem em poucos segundos — não é preciso reiniciar o bot.",
            cor=CORES["desligado"], wraplength=580,
        ).pack(anchor="w", padx=14, pady=(4, 12))

    def _construir_aviso_lista_fixa(self) -> None:
        """Banner para quando o `.env` fixa a lista e manda na base de dados."""
        caixa = tk.Frame(self, bg="#302d41")
        caixa.pack(fill="x", padx=14, pady=(14, 0))
        ids = ", ".join(str(uid) for uid in self.fixos) or "(nenhum)"
        tk.Label(
            caixa,
            text=(
                f"⚠  A lista está fixada em ALLOWED_USER_IDS ({self.origem_fixos}): {ids}.\n"
                "Enquanto assim for, o bot ignora as permissões geridas aqui."
            ),
            bg="#302d41", fg=CORES["aviso"], font=("Segoe UI", 9), justify="left",
            wraplength=560,
        ).pack(anchor="w", padx=10, pady=(8, 4))

        if self.origem_fixos == "sistema":
            tk.Label(
                caixa,
                text="Vem de uma variável de ambiente do sistema — tem de ser lá que a apaga.",
                bg="#302d41", fg=CORES["texto"], font=("Segoe UI", 9), wraplength=560,
                justify="left",
            ).pack(anchor="w", padx=10, pady=(0, 8))
        else:
            self._botao(caixa, "Passar a gestão para o painel", self.libertar_lista_fixa).pack(
                anchor="w", padx=10, pady=(0, 8)
            )

    # -- dados -------------------------------------------------------------
    def _dizer(self, texto: str, cor: str | None = None) -> None:
        self.estado.config(text=texto, fg=cor or CORES["texto"])

    def recarregar(self) -> None:
        try:
            self.registos = acessos.listar(self.conexao)
        except Exception as exc:  # noqa: BLE001 — mostrar é melhor do que rebentar
            self._dizer(f"Não consegui ler a lista: {exc}", CORES["erro"])
            return

        self.lista.delete(0, "end")
        for registo in self.registos:
            coroa = "👑" if registo["is_owner"] else "  "
            nome = registo["label"] or "sem nome"
            desde = str(registo["granted_at"] or "")[:10]
            self.lista.insert("end", f" {coroa} {registo['user_id']:<12} {nome:<20} desde {desde}")

        if not self.registos:
            self._dizer(
                "Ninguém na lista: o bot está aberto e a primeira pessoa que lhe "
                "escrever fica dona. Adicione-se aqui para o fechar já.",
                CORES["aviso"],
            )
        else:
            self._dizer(f"{len(self.registos)} utilizador(es) com acesso.", CORES["ligado"])

    def _seleccionado(self) -> dict | None:
        indices = self.lista.curselection()
        if not indices:
            self._dizer("Escolha primeiro alguém da lista.", CORES["aviso"])
            return None
        return self.registos[indices[0]]

    # -- acções ------------------------------------------------------------
    def adicionar(self) -> None:
        bruto = self.campo_id.get().strip()
        if not bruto:
            self._dizer("Escreva o id do Telegram (só números).", CORES["aviso"])
            return
        try:
            user_id = int(bruto)
        except ValueError:
            self._dizer(f"«{bruto}» não é um id — o id é um número.", CORES["erro"])
            return

        nome = self.campo_nome.get().strip()
        try:
            dono = acessos.adicionar(self.conexao, user_id, nome)
        except Exception as exc:  # noqa: BLE001
            self._dizer(f"Não consegui gravar: {exc}", CORES["erro"])
            return

        self.campo_id.delete(0, "end")
        self.campo_nome.delete(0, "end")
        self.recarregar()
        extra = " (é o dono, por ser o primeiro da lista)" if dono else ""
        self._dizer(f"✅ {user_id}{f' — {nome}' if nome else ''} já pode falar com o bot{extra}.",
                    CORES["ligado"])
        self.registar(f"👥 Acesso dado a {user_id}{f' ({nome})' if nome else ''}.\n", "ok")

    def remover(self) -> None:
        registo = self._seleccionado()
        if registo is None:
            return
        if registo["is_owner"]:
            self._dizer(
                "O dono não pode ser removido. Passe primeiro a outra pessoa "
                "com «Tornar dono».",
                CORES["aviso"],
            )
            return

        nome = registo["label"] or str(registo["user_id"])
        if not messagebox.askyesno(
            "Utilizadores", f"Retirar o acesso a {nome}?", parent=self
        ):
            return

        try:
            retirado = acessos.remover(self.conexao, registo["user_id"])
        except Exception as exc:  # noqa: BLE001
            self._dizer(f"Não consegui remover: {exc}", CORES["erro"])
            return

        self.recarregar()
        if retirado:
            self._dizer(f"✅ {registo['user_id']} já não tem acesso.", CORES["ligado"])
            self.registar(f"👥 Acesso retirado a {registo['user_id']}.\n", "aviso")
        else:
            self._dizer("Não havia nada para remover.", CORES["aviso"])

    def tornar_dono(self) -> None:
        registo = self._seleccionado()
        if registo is None:
            return
        if registo["is_owner"]:
            self._dizer("Essa pessoa já é a dona.", CORES["aviso"])
            return

        nome = registo["label"] or str(registo["user_id"])
        if not messagebox.askyesno(
            "Utilizadores",
            f"Tornar {nome} o dono do assistente?\n\n"
            "O dono não pode ser removido da lista — quem o era deixa de ter "
            "essa protecção.",
            parent=self,
        ):
            return

        try:
            mudou = acessos.definir_dono(self.conexao, registo["user_id"])
        except Exception as exc:  # noqa: BLE001
            self._dizer(f"Não consegui mudar o dono: {exc}", CORES["erro"])
            return

        self.recarregar()
        if mudou:
            self._dizer(f"👑 {nome} é agora o dono.", CORES["ligado"])
            self.registar(f"👥 {registo['user_id']} passou a dono.\n", "ok")

    def libertar_lista_fixa(self) -> None:
        """Esvazia o `ALLOWED_USER_IDS` do `.env` e traz os ids para a base de dados."""
        if not messagebox.askyesno(
            "Utilizadores",
            "A lista passa a ser gerida aqui (e pelos comandos /allow e /revoke).\n\n"
            f"Os ids que estão no .env ({len(self.fixos)}) são copiados para a base "
            "de dados e a linha ALLOWED_USER_IDS fica vazia. É guardada uma cópia "
            "do ficheiro em .env.bak.\n\n"
            "O bot tem de ser reiniciado para a mudança valer. Continuar?",
            parent=self,
        ):
            return

        try:
            ids = acessos.esvaziar_lista_fixa()
            acessos.importar(self.conexao, ids)
        except acessos.ErroAcesso as exc:
            self._dizer(str(exc), CORES["erro"])
            return

        self.fixos, self.origem_fixos = [], ""
        self.recarregar()
        self._dizer(
            f"✅ {len(ids)} id(s) copiado(s) para a base de dados. "
            "Pare e volte a ligar o assistente para a mudança valer.",
            CORES["ligado"],
        )
        self.registar(
            "👥 ALLOWED_USER_IDS esvaziado no .env; o acesso passa a ser gerido no "
            "painel (reinicie o assistente).\n",
            "aviso",
        )
        messagebox.showinfo(
            "Utilizadores",
            "Feito. Reabra esta janela depois de reiniciar o assistente.",
            parent=self,
        )

    # -- fecho -------------------------------------------------------------
    def fechar(self) -> None:
        try:
            self.conexao.close()
        except Exception:  # noqa: BLE001 — fechar nunca pode falhar
            pass
        self.destroy()


def main() -> int:
    if sys.platform != "win32":
        print("Este painel foi feito para Windows.")
    root = tk.Tk()
    Painel(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
