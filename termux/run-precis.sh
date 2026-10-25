#!/data/data/com.termux/files/usr/bin/bash
# Un passage de précision. --auto : silencieux, et seulement si le dernier passage date de plus de 2 h.
QF="$HOME/.qf"; REPO="$(cat "$QF/repo" 2>/dev/null || echo "$HOME/vpf")"; LOG="$QF/precis.log"
mkdir -p "$QF"
if [ "${1:-}" = "--auto" ] && [ -f "$QF/last_ok" ] && [ $(( $(date +%s) - $(stat -c %Y "$QF/last_ok") )) -lt 7200 ]; then exit 0; fi
# un seul passage à la fois (verrou), un verrou de plus de 1 h est considéré comme abandonné
if ! mkdir "$QF/lock" 2>/dev/null; then
  [ $(( $(date +%s) - $(stat -c %Y "$QF/lock" 2>/dev/null || date +%s) )) -gt 3600 ] && rm -rf "$QF/lock" && mkdir "$QF/lock" || exit 0
fi
trap 'rm -rf "$QF/lock"' EXIT
# pas de réseau : on reporte (aucun réveil forcé du téléphone, compatible économie d'énergie)
curl -s -m 15 -o /dev/null https://github.com || { echo "$(date '+%F %T') pas de réseau, passage reporté" >> "$LOG"; exit 0; }
{
  echo "===== $(date '+%F %T')"
  cd "$REPO" && git pull --rebase -q 2>&1 | tail -2
  timeout 1800 python -m quote_finder.phone_precise --repo "$REPO" 2>&1 | grep -v "^ *$" | tail -15
} >> "$LOG" 2>&1
RC=$?
tail -n 3000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
touch "$QF/last_ok"
LAST="$(grep -E "extraits recalés" "$LOG" | tail -1)"
if [ "${1:-}" != "--auto" ]; then tail -n 12 "$LOG"; fi
if command -v termux-notification >/dev/null && echo "$LAST" | grep -qE "recalés : [1-9]"; then
  timeout 10 termux-notification --id 4242 --title "Vidéo-presse : extraits précisés" --content "$(echo "$LAST" | cut -c1-140)" >/dev/null 2>&1
fi
exit $RC
