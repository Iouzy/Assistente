#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Abre o painel de controlo do assistente. É a este ficheiro que o atalho do
# menu de aplicações (criado por instalar.sh) chama — duplo clique, sem
# terminal, sem escrever nada.
# ---------------------------------------------------------------------------
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ"

avisar() {
    # Sem terminal visível (é o caso normal, vindo do atalho), uma mensagem
    # em stderr não chega a lado nenhum — tenta um diálogo, se houver um.
    echo "$1" >&2
    if command -v zenity >/dev/null 2>&1; then
        zenity --error --text="$1" 2>/dev/null || true
    elif command -v notify-send >/dev/null 2>&1; then
        notify-send "Assistente" "$1" || true
    fi
}

if [ ! -x .venv/bin/python ]; then
    avisar "Falta preparar o assistente. Abra um terminal nesta pasta e corra: bash linux/instalar.sh"
    exit 1
fi

exec .venv/bin/python painel.py
