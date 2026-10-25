"""ETAT.md : ce qui bloque (quotas, YouTube, téléphone, articles...), pourquoi, quand ça se débloque, quoi faire.

Écrit par GitHub à la fin de chaque recherche et de chaque Diagnostic, et par le téléphone quand son état change.
Heures données en heure italienne (Europe/Rome).

    python -m quote_finder.status            -> ETAT.md
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import time
from pathlib import Path

from . import quota

try:
    from zoneinfo import ZoneInfo
    ROME = ZoneInfo("Europe/Rome")
except Exception:  # noqa: BLE001
    ROME = dt.timezone(dt.timedelta(hours=1))

PHONE_FILE = Path("state/telephone.json")


def hm(t: float) -> str:
    """« à 9 h 00 » aujourd'hui, « demain à 9 h 00 », ou date complète."""
    d = dt.datetime.fromtimestamp(t, ROME)
    now = dt.datetime.now(ROME)
    h = f"{d.hour} h {d.minute:02d}"
    if d.date() == now.date():
        return f"à {h}"
    if d.date() == (now + dt.timedelta(days=1)).date():
        return f"demain à {h}"
    return f"le {d.strftime('%d/%m')} à {h}"


def ago(t: float) -> str:
    s = max(0, time.time() - t)
    if s < 3600:
        return f"il y a {int(s // 60)} min"
    if s < 86400:
        return f"il y a {int(s // 3600)} h"
    return f"il y a {int(s // 86400)} j"


def _keys_section(st: quota.QuotaState) -> tuple[list[str], list[str]]:
    L, todo = [], []
    now = time.time()
    reset = hm(quota.next_reset())
    groups: dict[str, list] = {}
    for k, e in st.data.get("keys", {}).items():
        groups.setdefault(k.split(":")[0], []).append(e)
    names = {"gemini": "Gemini (IA)", "youtube": "YouTube (recherche)", "groq": "Groq (relais IA)", "cerebras": "Cerebras (relais IA)",
             "openrouter": "OpenRouter (relais IA)", "fallback": "Relais IA", "guardian": "The Guardian", "nyt": "New York Times"}
    for api in ("gemini", "youtube", "groq", "cerebras", "openrouter", "fallback", "guardian", "nyt"):
        es = sorted(groups.get(api, []), key=lambda e: e.get("label", ""))
        if not es:
            continue
        dead = [e for e in es if e.get("until", 0) > now]
        partial = [e for e in es if e not in dead and e.get("models")]
        icon = "❌" if len(dead) == len(es) else "⚠️" if dead or partial else "✅"
        line = f"- {icon} **{names[api]}** : {len(es) - len(dead)}/{len(es)} clé(s) utilisable(s)"
        if api == "youtube":
            left = sum(max(0, quota.YT_DAILY_UNITS - e.get("units", 0)) for e in es if e not in dead)
            line += f" · ≈ {left // 100} recherches restantes aujourd'hui"
        L.append(line)
        for e in dead:
            why = " ".join(str(e.get("last", "")).split())[:80]
            L.append(f"  - {e.get('label', '?')} : épuisée ({why or 'quota du jour'}) → se débloque {hm(e['until'])}")
        for e in partial:
            soon = min(e["models"].values())
            L.append(f"  - {e.get('label', '?')} : {len(e['models'])} modèle(s) au bout du quota du jour, les autres modèles "
                     f"restent utilisés → tout revient {hm(soon)}")
        if len(dead) == len(es):
            todo.append(f"{names[api]} : toutes les clés sont vides → rien à faire, retour automatique {reset}"
                        + (" ; en attendant, Groq prend le relais pour le texte" if api == "gemini" else ""))
    if not groups:
        L.append("- ℹ️ Pas encore d'information sur les clés : lance *Actions → Diagnostic*.")
    L.append(f"- ℹ️ Remise à zéro des quotas gratuits Google : chaque jour {reset.replace('demain ', '').replace('à ', 'à ')} (minuit en Californie).")
    return L, todo


def _latest_lot(results: Path) -> Path | None:
    lots = sorted((p for p in results.glob("20*") if p.is_dir()), reverse=True)
    return lots[0] if lots else None


def _lot_section(results: Path) -> tuple[list[str], list[str], dict]:
    L, todo, agg = [], [], {"via": {}, "unreadable": 0, "videos_none": {}, "searches": 0, "clips": 0}
    lot = _latest_lot(results)
    if not lot:
        return ["- ℹ️ Aucun lancement pour l'instant."], todo, agg
    for rj in sorted(lot.glob("*/results.json")):
        d = json.loads(rj.read_text(encoding="utf-8"))
        agg["searches"] += 1
        diag = (d.get("plan") or {}).get("diag") or {}
        for k, n in (diag.get("articles_read_via") or {}).items():
            agg["via"][k] = agg["via"].get(k, 0) + n
        for v in d.get("unverifiable_videos", []):
            r = str(v.get("reason", ""))
            key = ("limite par minute de Gemini (429)" if "429" in r else "vidéo trop longue (> 60 min)" if "trop longue" in r
                   else "temps imparti écoulé" if "temps" in r else "quota IA épuisé" if "quota" in r else "autre erreur")
            agg["videos_none"][key] = agg["videos_none"].get(key, 0) + 1
        agg["clips"] += sum(len(v.get("moments", [])) for v in d.get("videos", []))
    errs = sorted(p.parent.name for p in lot.glob("*/ERREUR.txt"))
    icon = "❌" if errs and len(errs) == agg["searches"] + len(errs) else "⚠️" if errs or agg["videos_none"] else "✅"
    L.append(f"- {icon} Lot **{lot.name}** : {agg['searches']} recherche(s) réussie(s), {agg['clips']} extrait(s) vidéo"
             + (f", {len(errs)} recherche(s) en échec ({', '.join(errs)})" if errs else ""))
    for k, n in agg["videos_none"].items():
        L.append(f"  - {n} vidéo(s) non analysée(s) : {k}")
        if "429" in k:
            todo.append("Vidéos refusées par la limite par minute de Gemini : relance la même recherche plus tard "
                        "(les vidéos déjà analysées sont en cache, ça ira vite)")
    via = agg["via"]
    if via:
        ok = sum(n for k, n in via.items() if not k.startswith("illisible"))
        bad = {k.replace("illisible : ", ""): n for k, n in via.items() if k.startswith("illisible")}
        agg["unreadable"] = sum(bad.values())
        icon = "✅" if not bad else "⚠️" if ok else "❌"
        L.append(f"- {icon} **Articles** : {ok} lu(s) ("
                 + ", ".join(f"{k} ×{n}" for k, n in via.items() if not k.startswith("illisible")) + ")"
                 + (f", {sum(bad.values())} illisible(s) : " + ", ".join(f"{k} ×{n}" for k, n in bad.items()) if bad else ""))
        if bad and not via.get("gemini"):
            todo.append("Articles illisibles et aucun lu par Gemini : quota Gemini probablement saturé pendant le lancement "
                        "→ relance plus tard ou le lendemain")
    return L, todo, agg


def _phone_section(results: Path) -> tuple[list[str], list[str]]:
    L, todo = [], []
    pend = 0
    try:
        from .phone_precise import pending
        for rj in results.glob("*/*/results.json"):
            for v in json.loads(rj.read_text(encoding="utf-8")).get("videos", []):
                if v.get("platform") == "youtube":
                    pend += sum(1 for m in v.get("moments", []) if pending(m) and m.get("timing") != "non retrouvé")
    except Exception:  # noqa: BLE001
        pass
    try:
        ph = json.loads(PHONE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ph = {}
    if not ph:
        L.append("- ℹ️ Aucun passage du téléphone enregistré. Pour l'activer : dans Termux, "
                 "`cd ~/vpf && git pull origin main && bash termux/install-precis.sh`")
    else:
        last = ph.get("last_run", 0)
        icon = "⚠️" if time.time() - last > 36 * 3600 else "✅"
        L.append(f"- {icon} Dernier passage {ago(last)} ({ph.get('network', 'réseau ?')}) : {ph.get('last_result', '')}")
        if time.time() - last > 36 * 3600:
            todo.append("Téléphone : aucun passage depuis plus de 36 h → ouvre Termux (passage automatique) ou lance `qf precis`")
        for net, until in (ph.get("blocked") or {}).items():
            if until > time.time():
                L.append(f"  - ⚠️ {net} refusé par YouTube → nouvel essai automatique {hm(until)} "
                         "(les autres réseaux restent essayés : passe sur les données mobiles pour aller plus vite)")
        L.append(f"  - Passage automatique : {ph.get('auto', '?')}")
    L.append(f"- {'⏳' if pend else '✅'} Extraits en attente de recalage par le téléphone : {pend}")
    return L, todo


def build(results: Path = Path("results"), out: Path = Path("ETAT.md")) -> Path:
    st = quota.state()
    k_lines, k_todo = _keys_section(st)
    lot_lines, lot_todo, _ = _lot_section(results)
    ph_lines, ph_todo = _phone_section(results)
    todo = k_todo + lot_todo + ph_todo
    now = dt.datetime.now(ROME).strftime("%d/%m/%Y à %H:%M")
    L = ["# État du système", "",
         f"_Mis à jour le {now} (heure italienne), automatiquement après chaque recherche, Diagnostic ou passage du téléphone._", "",
         "## En bref", ""]
    L += [f"- 👉 {t}" for t in todo] or ["- ✅ Rien ne bloque. Tu peux lancer tes recherches."]
    L += ["", "## Clés et quotas", ""] + k_lines
    L += ["", "## YouTube depuis GitHub", "",
          "- ❌ Sous-titres et téléchargements YouTube bloqués depuis GitHub (constaté le 24/10/2026, c'est permanent) → "
          "**normal, rien à faire** : Gemini analyse les vidéos (±1 à ±5 s) et le téléphone recale ensuite sur les sous-titres (±1-2 s)."]
    L += ["", "## Téléphone (précision des extraits)", ""] + ph_lines
    L += ["", "## Dernier lancement", ""] + lot_lines
    L += ["", "---", "Détail des clés : [state/QUOTAS.md](state/QUOTAS.md) · Résultats : [results/README.md](results/README.md)", ""]
    out.write_text("\n".join(L), encoding="utf-8")
    return out


def main(argv=None) -> int:
    p = build()
    print(p.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
