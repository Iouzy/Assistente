"""Ponto de entrada único do programa compilado.

Um `.exe` só tem um ponto de entrada, mas o assistente são dois programas: o
painel de controlo (a janela) e o bot (o processo que fala com o Telegram).
A solução é o executável relançar-se a si próprio:

    Assistente.exe            → abre o painel
    Assistente.exe --bot      → corre o bot, sem janela

É o que o painel faz ao carregar em «Ligar». Mantém-se assim tudo o que já
funcionava com dois processos separados: a consola ao vivo lê o `stdout` do
filho, e «Parar» continua a ser o ficheiro `.stop-assistente` que o bot vigia,
com encerramento ordenado e memória gravada.

A correr a partir do código isto não é obrigatório — `python main.py` e
`python painel.py` continuam a funcionar como sempre — mas serve para testar
o mesmo caminho que o `.exe` vai usar.
"""

from __future__ import annotations

import multiprocessing
import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # Obrigatório antes de tudo o resto num executável do PyInstaller: sem
    # isto, cada processo filho criado com `multiprocessing` volta a correr o
    # programa desde o início em vez do que lhe foi pedido — o painel em modo
    # nativo (pywebview) cria um, e o resultado era uma cascata de janelas.
    multiprocessing.freeze_support()

    if "--versao" in argv or "--version" in argv:
        import versao
        print(versao.VERSAO)
        return 0

    # A pasta de dados tem de existir e estar povoada antes de o `config.py`
    # ser importado — é ele que lê o `.env` de lá, no momento da importação.
    import caminhos

    importados = caminhos.preparar()

    modo_bot = "--bot" in argv
    if importados and not modo_bot:
        # Só o painel tem onde dizer isto a alguém.
        print("Importado de uma instalação anterior: " + ", ".join(importados))

    if modo_bot:
        import main as bot_main
        return bot_main.main()

    import painel
    return painel.main()


if __name__ in {"__main__", "__mp_main__"}:
    raise SystemExit(main())
