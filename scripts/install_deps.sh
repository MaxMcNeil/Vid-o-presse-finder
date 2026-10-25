#!/bin/bash
# Installation des dépendances sur GitHub, bornée dans le temps : un miroir lent ou en panne ne doit jamais
# consommer le temps de la recherche (le 25/10/2026, une installation bloquée 37 min a fait annuler une recherche).
#   bash scripts/install_deps.sh [requirements.txt]
set -u
REQ="${1:-requirements.txt}"
T0=$(date +%s)
# ffmpeg : utile seulement pour l'audio hors YouTube et les extraits ; son absence n'empêche pas la recherche
if ! command -v ffmpeg >/dev/null; then
  for i in 1 2; do
    timeout 180 sudo apt-get update -qq -o Acquire::Retries=2 -o Acquire::http::Timeout=20 >/dev/null 2>&1
    timeout 180 sudo apt-get install -y -qq ffmpeg >/dev/null 2>&1 && break
  done
  command -v ffmpeg >/dev/null || echo "::warning::ffmpeg non installé (miroir lent) : l'audio hors YouTube sera sauté, le reste fonctionne"
fi
ok=0
for i in 1 2 3; do
  # curl-cffi : nécessaire à yt-dlp pour certains sites (Dailymotion « impersonation »)
  if timeout 300 pip install -q --timeout 30 --retries 4 -r "$REQ" && \
     timeout 180 pip install -q --timeout 30 --retries 4 -U "yt-dlp[default,curl-cffi]"; then ok=1; break; fi
  echo "::warning::installation Python : essai $i échoué, nouvel essai"
  sleep $((i * 10))
done
[ "$ok" = 1 ] || { echo "::error::Installation des dépendances impossible (PyPI injoignable ?) : relance le workflow"; exit 1; }
echo "Dépendances installées en $(( $(date +%s) - T0 )) s"
