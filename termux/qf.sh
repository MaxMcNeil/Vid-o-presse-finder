#!/data/data/com.termux/files/usr/bin/bash
# Quote Finder sur Termux.  qf | qf keys | qf update
REPO="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
CONF="$HOME/.quote_finder"
ENVF="$CONF/keys.env"
mkdir -p "$CONF"
[ -f "$ENVF" ] && . "$ENVF"

KEYVARS="GEMINI_API_KEYS YOUTUBE_API_KEYS FALLBACK_LLM_KEYS GUARDIAN_API_KEYS NYT_API_KEYS"

save_keys() {
  : > "$ENVF"; chmod 600 "$ENVF"
  for v in $KEYVARS FALLBACK_LLM_PROVIDER; do
    [ -n "${!v}" ] && printf 'export %s="%s"\n' "$v" "${!v}" >> "$ENVF"
  done
}

case "$1" in
  keys)
    echo "Colle tes clés sur UNE ligne (séparées par des espaces ou virgules). Entrée = garder l'actuel / ignorer."
    for v in $KEYVARS; do
      cur="${!v}"; hint=""; [ -n "$cur" ] && hint=" [déjà défini]"
      read -rp "$v$hint : " val
      if [ -n "$val" ]; then
        printf -v "$v" '%s' "$(echo "$val" | tr -s ' ,;' ',' | sed 's/^,//; s/,$//')"
        export "$v"
      fi
    done
    [ -n "$FALLBACK_LLM_KEYS" ] && [ -z "$FALLBACK_LLM_PROVIDER" ] && FALLBACK_LLM_PROVIDER=groq
    save_keys
    echo "Clés enregistrées dans $ENVF (lisible par toi seul)."
    exit 0 ;;
  update)
    cd "$REPO" && git pull --rebase -q && pip install -q -r requirements-termux.txt && echo "À jour."
    exit 0 ;;
esac

if [ -z "$GEMINI_API_KEYS" ]; then
  echo "Aucune clé Gemini. Lance d'abord :  qf keys"; exit 1
fi

if [ -n "$1" ] && [ -n "$2" ]; then
  PERSON="$1"; CLAIM="$2"; ALSO="$3"; ANYONE="${4:-n}"; MAXV="${5:-30}"
else
  read -rp "Personne (ex: Bill Clinton) : " PERSON
  read -rp "Sujet / affirmation : " CLAIM
  read -rp "Autres locuteurs acceptés, séparés par des virgules (Entrée = aucun) : " ALSO
  read -rp "Accepter n'importe quel locuteur ? (o/N) : " ANYONE
  read -rp "Nombre de vidéos à analyser [30] : " MAXV
  MAXV="${MAXV:-30}"
fi
[ -z "$PERSON" ] || [ -z "$CLAIM" ] && { echo "Personne et sujet obligatoires."; exit 1; }

args=(--person "$PERSON" --claim "$CLAIM" --also "$ALSO" --max-videos "$MAXV" --out "$REPO/results")
case "$ANYONE" in o|O|y|Y|oui|yes) args+=(--anyone) ;; esac

command -v termux-wake-lock >/dev/null && termux-wake-lock   # évite qu'Android coupe le calcul écran éteint
export QF_CACHE_DIR="$CONF/cache"
cd "$REPO" || exit 1
python -m quote_finder "${args[@]}" 2>&1 | tee "$CONF/last.log"
command -v termux-wake-unlock >/dev/null && termux-wake-unlock

OUT="$(ls -td "$REPO"/results/*/ 2>/dev/null | head -1)"
[ -z "$OUT" ] && { echo "Pas de rapport produit : voir $CONF/last.log"; exit 1; }
if [ -d "$HOME/storage/downloads" ]; then
  DEST="$HOME/storage/downloads/quote_finder_$(basename "$OUT").md"
  cp "$OUT/report.md" "$DEST" && echo "Rapport copié dans Téléchargements : $(basename "$DEST")"
fi
echo; head -n 60 "$OUT/report.md"
echo; read -rp "Envoyer les résultats sur GitHub ? (o/N) : " PUSH
case "$PUSH" in o|O|y|Y)
  git add results && git commit -qm "résultats (Termux)" && git pull --rebase -q && git push -q && echo "Envoyé." ;;
esac
