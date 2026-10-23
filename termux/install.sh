#!/data/data/com.termux/files/usr/bin/bash
# Installation sur Termux (une seule fois) : bash termux/install.sh
set -e
cd "$(dirname "$(readlink -f "$0")")/.."
pkg update -y
pkg install -y python ffmpeg git unzip
pip install -r requirements-termux.txt
mkdir -p "$HOME/.quote_finder"
chmod +x termux/qf.sh
ln -sf "$PWD/termux/qf.sh" "$PREFIX/bin/qf"
echo
echo "Installé. Étapes suivantes :"
echo "  1) qf keys     -> enregistre tes clés API (une seule fois)"
echo "  2) qf          -> lance une recherche"
