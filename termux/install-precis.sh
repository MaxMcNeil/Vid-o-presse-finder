#!/data/data/com.termux/files/usr/bin/bash
# Installation (et réinstallation) de la précision par le téléphone. Sans danger à relancer.
#   bash ~/vpf/termux/install-precis.sh
set -u
REPO="${QF_REPO:-$HOME/vpf}"
QF="$HOME/.qf"
mkdir -p "$QF" "$HOME/.termux/boot" "$PREFIX/bin"
echo "1/5 Paquets Termux…"
pkg install -y python git curl >/dev/null 2>&1 || { echo "✘ pkg install a échoué : lance 'pkg update' puis relance ce script"; exit 1; }
pkg install -y termux-api >/dev/null 2>&1 || true
echo "2/5 Bibliothèques Python (sous-titres YouTube)…"
pip install -q --upgrade "youtube-transcript-api>=1.0,<2" "requests>=2.31,<3" yt-dlp || { echo "✘ pip a échoué"; exit 1; }
# un ancien verrou de veille laissé par la 1re version : relâché (économie d'énergie)
command -v termux-wake-unlock >/dev/null && termux-wake-unlock 2>/dev/null
[ -d "$REPO/.git" ] || { echo "✘ dépôt introuvable dans $REPO (QF_REPO=chemin pour un autre dossier)"; exit 1; }
# configuration Git réparée : une seule branche suivie (origin/main) — évite « Cannot rebase onto multiple branches »
git -C "$REPO" config --unset-all branch.main.merge 2>/dev/null
git -C "$REPO" config branch.main.remote origin
git -C "$REPO" config branch.main.merge refs/heads/main

echo "3/5 Commande qf…"
# anciennes versions (écoute GitHub par ntfy) : arrêtées et retirées, elles ne servent plus
pkill -f "qf listen" 2>/dev/null; pkill -f "quote_finder.phone " 2>/dev/null
rm -f "$HOME/.termux/boot/"*qf-listen* "$HOME/.termux/boot/"*quote* 2>/dev/null
cp "$REPO/termux/run-precis.sh" "$QF/run-precis.sh" && chmod +x "$QF/run-precis.sh"
rm -f "$PREFIX/bin/qf"   # ancien raccourci (1re version) éventuellement cassé
cp "$REPO/termux/qf" "$PREFIX/bin/qf" && chmod +x "$PREFIX/bin/qf" && hash -r 2>/dev/null
[ -x "$PREFIX/bin/qf" ] || echo "⚠ commande qf non installée : utilise « bash $REPO/termux/qf precis »"
echo "$REPO" > "$QF/repo"

echo "4/5 Passage automatique (compatible économie d'énergie)…"
MODE="aucun"
if command -v termux-job-scheduler >/dev/null 2>&1 && timeout 20 termux-job-scheduler --pending >/dev/null 2>&1; then
  # Android JobScheduler : respecte l'économie d'énergie (Android choisit le moment), survit au redémarrage
  timeout 20 termux-job-scheduler --cancel --job-id 4242 >/dev/null 2>&1
  timeout 20 termux-job-scheduler --job-id 4242 --script "$QF/run-precis.sh" --period-ms 3600000 \
    --network any --battery-not-low false --persisted true >/dev/null 2>&1 && MODE="Android (toutes les heures environ)"
fi
case "$MODE" in aucun) echo "à l'ouverture de Termux" ;; *) echo "Android toutes les heures environ + à l'ouverture de Termux" ;; esac > "$QF/auto_mode"
# au démarrage du téléphone (si l'appli Termux:Boot est installée) : replanifie et fait un passage
cat > "$HOME/.termux/boot/qf-precis.sh" <<BOOT
#!/data/data/com.termux/files/usr/bin/bash
sleep 120
command -v termux-job-scheduler >/dev/null && timeout 20 termux-job-scheduler --job-id 4242 --script "$QF/run-precis.sh" --period-ms 3600000 --network any --persisted true >/dev/null 2>&1
"$QF/run-precis.sh" --auto
BOOT
chmod +x "$HOME/.termux/boot/qf-precis.sh"
# filet de sécurité : à chaque ouverture de Termux, passage en arrière-plan si le dernier date de plus de 2 h
grep -q "qf-precis-hook" "$HOME/.bashrc" 2>/dev/null || cat >> "$HOME/.bashrc" <<'HOOK'
# qf-precis-hook : précision des extraits (Vid-o-presse-finder) en arrière-plan si nécessaire
( [ -x "$HOME/.qf/run-precis.sh" ] && "$HOME/.qf/run-precis.sh" --auto >/dev/null 2>&1 & ) 2>/dev/null
HOOK

echo "5/5 Premier passage (peut prendre quelques minutes la première fois)…"
"$QF/run-precis.sh"
echo
echo "✔ Installé. Passage automatique : $MODE + à chaque ouverture de Termux (+ au démarrage avec Termux:Boot)."
[ "$MODE" = "aucun" ] && echo "  Conseil : installe l'appli « Termux:API » (même source que Termux), puis relance ce script pour le passage automatique."
echo "  Commandes : qf precis (maintenant) · qf statut · qf maj"
