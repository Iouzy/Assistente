"""Painel de controlo do assistente — ligar, desligar e ver a consola.

Uma janela simples (tkinter, que vem com o Python) para não ser preciso andar
sempre pela linha de comandos. Extensão `.pyw` para não abrir consola nenhuma.

O bot corre como processo-filho, com a saída canalizada para a caixa de texto.
Ao carregar em Parar, o pedido é feito através do ficheiro-sentinela que o
`main.py` vigia — encerramento ordenado, com a memória gravada. Só se o
processo não obedecer é que é terminado à força.

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
            if any(f.startswith("windows/painel") for f in alterados):
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


def main() -> int:
    if sys.platform != "win32":
        print("Este painel foi feito para Windows.")
    root = tk.Tk()
    Painel(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
