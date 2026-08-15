#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Prepara o assistente para correr em Linux/Ubuntu: cria o ambiente virtual,
# instala as dependências (bot + painel) e um atalho para abrir o painel com
# um duplo clique, sem mais linha de comandos depois disto.
#
# Corre-se uma vez:  bash linux/instalar.sh
# ---------------------------------------------------------------------------
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ"

if ! command -v python3 >/dev/null 2>&1; then
    echo "ERRO: não encontrei o python3. Instale-o com: sudo apt install python3 python3-venv"
    exit 1
fi

if [ ! -d .venv ]; then
    echo "A criar o ambiente virtual em .venv..."
    python3 -m venv .venv
fi

echo "A instalar as dependências (bot + painel)..."
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt -r requirements-painel.txt

chmod +x linux/iniciar_painel.sh

# Atalho para o menu de aplicações, com o caminho absoluto deste repositório
# já preenchido — só faz sentido gerado aqui, não é possível deixá-lo pronto
# no repositório porque cada pessoa clona para uma pasta diferente.
DESTINO_ATALHOS="$HOME/.local/share/applications"
mkdir -p "$DESTINO_ATALHOS"
ATALHO="$DESTINO_ATALHOS/assistente-painel.desktop"
cat > "$ATALHO" <<EOF
[Desktop Entry]
Type=Application
Name=Assistente — Painel de Controlo
Comment=Ligar, desligar e configurar o assistente pessoal
Exec=$RAIZ/linux/iniciar_painel.sh
Path=$RAIZ
Icon=utilities-system-monitor
Terminal=false
Categories=Utility;
EOF
chmod +x "$ATALHO"

echo
echo "Pronto. O assistente já tem um atalho no menu de aplicações:"
echo "  Assistente — Painel de Controlo"
echo
echo "Para o ter também no Ambiente de Trabalho, copie o ficheiro:"
echo "  cp \"$ATALHO\" ~/Desktop/  (e marque-o como fidedigno se o Ubuntu pedir)"
echo
echo "Abra o painel a partir do menu, ou com:  ./linux/iniciar_painel.sh"
echo "As credenciais (token do Telegram, chave da DeepSeek) preenchem-se na"
echo "aba «Credenciais» do painel — não é preciso editar nenhum ficheiro."
