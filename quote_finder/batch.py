"""Plusieurs recherches à la fois.

Format (séparateur « ; » ou retour à la ligne, champs séparés par « | ») :
    cible | sujet | type
    La Suisse | abandonne Microsoft pour la souveraineté numérique | pays
    Jordan Bardella | mensonges, chiffres erronés
    Microsoft | espionnage des données européennes | entité
    souveraineté numérique européenne
Le sujet et le type sont facultatifs (type deviné par l'IA si absent).

    python -m quote_finder.batch matrix "<recherches>"   -> JSON pour la matrice GitHub Actions
    python -m quote_finder.batch merge results/<lot>     -> INDEX.md + montage.json global du lot
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .models import TARGET_TYPES
from .report import write_montage
from .util import normalize, slugify

MAX_SEARCHES = 10
TYPE_ALIASES = {"personne": "personne", "person": "personne", "pers": "personne", "p": "personne",
                "pays": "pays", "country": "pays", "ville": "pays", "region": "pays",
                "entite": "entite", "entity": "entite", "entreprise": "entite", "organisation": "entite",
                "institution": "entite", "parti": "entite", "e": "entite",
                "sujet": "sujet", "theme": "sujet", "topic": "sujet", "s": "sujet", "auto": "auto", "": "auto"}


def parse(text: str, default_type: str = "auto") -> list[dict]:
    out = []
    lines = [ln for ln in (text or "").replace("\r", "").split("\n") if not ln.strip().startswith("#")]
    for raw in ";".join(lines).split(";"):
        parts = [p.strip() for p in raw.split("|")]
        if not parts or not parts[0] or parts[0].startswith("#"):
            continue
        target = parts[0]
        claim = parts[1] if len(parts) > 1 else ""
        t = normalize(parts[2]) if len(parts) > 2 else ""
        ttype = TYPE_ALIASES.get(t, t if t in TARGET_TYPES else default_type)
        if ttype not in TARGET_TYPES:
            ttype = "auto"
        out.append({"target": target, "claim": claim, "type": ttype})
    seen, uniq = set(), []
    for s in out:
        k = (normalize(s["target"]), normalize(s["claim"]))
        if k not in seen:
            seen.add(k)
            uniq.append(s)
    for i, s in enumerate(uniq[:MAX_SEARCHES], 1):
        s["index"] = i
        s["id"] = f"{i:02d}-" + slugify(f"{s['target']} {s['claim']}", 50)
    return uniq[:MAX_SEARCHES]


def merge(batch_dir: Path, title: str = "") -> Path:
    """Rassemble les recherches d'un lot : INDEX.md + montage.json/csv global (tous les extraits, triés par solidité)."""
    batch_dir = Path(batch_dir)
    rows, clips = [], []
    for d in sorted(p for p in batch_dir.iterdir() if p.is_dir()):
        rj = d / "results.json"
        if not rj.exists():
            err = d / "ERREUR.txt"
            try:
                info = json.loads((d / "recherche.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                info = {}
            msg = " ".join(err.read_text(encoding="utf-8").split())[:200] if err.exists() else "aucun résultat"
            rows.append((d.name, info.get("target", "?"), info.get("claim", "?"), 0, 0, 0, msg))
            continue
        data = json.loads(rj.read_text(encoding="utf-8"))
        mj = d / "montage.json"
        mc = json.loads(mj.read_text(encoding="utf-8")).get("clips", []) if mj.exists() else []
        clips += mc
        rows.append((d.name, data.get("target", ""), data.get("claim", ""), len(data.get("videos", [])), len(mc),
                     len(data.get("articles", [])), ""))
    clips.sort(key=lambda c: -c.get("score", 0))
    for i, c in enumerate(clips, 1):
        c["order"] = i
    write_montage(clips, batch_dir, {"batch": batch_dir.name, "searches": len(rows)})
    origine = {}
    try:   # lot lancé par OSINT-teleprompter- : lien vers le script du live (et synchro du montage)
        origine = json.loads((batch_dir / "ORIGINE.json").read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        pass
    if origine and not title:
        title = "🔗 OSINT : " + (origine.get("court") or origine.get("sujet") or "")[:80]
    L = [f"# {title or 'Lot de recherches'} — {batch_dir.name}", "",
         f"{len(rows)} recherche(s), {len(clips)} extrait(s) vidéo au total (`montage.json` / `montage.csv` de ce dossier).", "",
         "| # | Cible | Sujet | Vidéos | Extraits | Articles | Rapport |", "|---|---|---|---|---|---|---|"]
    if origine:
        dep = origine.get("depot") or "OSINT-teleprompter-"
        L[2:2] = [f"Lancé par [{dep}]({origine.get('run_url') or 'https://github.com/' + dep}) pour le live : "
                  f"« {(origine.get('sujet') or '')[:300]} » — script dans `prompter/{origine.get('dossier', '')}`.", ""]
    prec = {}
    for c in clips:
        prec[c.get("timing", "ia")] = prec.get(c.get("timing", "ia"), 0) + 1
    if clips:
        from .phone_precise import pending
        todo = 0
        for rj in batch_dir.glob("*/results.json"):  # extraits que le téléphone n'a pas encore tentés
            for v in json.loads(rj.read_text(encoding="utf-8")).get("videos", []):
                todo += sum(1 for m in v.get("moments", []) if v.get("platform") == "youtube" and pending(m)
                            and m.get("timing") != "non retrouvé")
        L.insert(3, f"Précision : {prec.get('sous-titres', 0)} extrait(s) calé(s) sur les sous-titres (±1-2 s), "
                    f"{prec.get('ia-affiné', 0)} affiné(s) par Gemini (±1 s), {prec.get('ia', 0)} estimé(s) (±5 s)."
                    + (f" ⏳ {todo} en attente de recalage par le téléphone (`qf precis`)." if todo else "") + "\n")
    for name, target, claim, nv, nc, na, err in rows:
        link = f"[report.md]({name}/report.md)" if not err else f"⚠️ {err}"
        L.append(f"| {name[:2]} | {target} | {claim} | {nv} | {nc} | {na} | {link} |")
    if clips:
        L += ["", "## Meilleurs extraits", ""]
        for c in clips[:15]:
            L.append(f"- [{c['start_tc'][:8]} → {c['end_tc'][:8]}]({c['link']}) · {c.get('channel', '')} · "
                     f"{c.get('speaker') or 'locuteur non identifié'} · score {c.get('score', 0):.2f}\n  > {c.get('quote', '')[:220]}")
    p = batch_dir / "INDEX.md"
    p.write_text("\n".join(L) + "\n", encoding="utf-8")
    return p


def mark_missing(batch_dir: Path, matrix_json: str) -> list[str]:
    """Recherches prévues (matrice du lancement) sans dossier de résultats : dossier + ERREUR.txt explicite."""
    batch_dir = Path(batch_dir)
    try:
        searches = json.loads(matrix_json or "[]")
    except ValueError:
        return []
    out = []
    for s in searches if isinstance(searches, list) else []:
        sid = str(s.get("id") or "")
        if not sid:
            continue
        d = batch_dir / sid
        d.mkdir(parents=True, exist_ok=True)
        (d / "recherche.json").write_text(json.dumps({k: s.get(k, "") for k in ("target", "claim", "type")},
                                                      ensure_ascii=False), encoding="utf-8")
        if not (d / "results.json").exists() and not (d / "ERREUR.txt").exists():
            (d / "ERREUR.txt").write_text("Recherche interrompue sans résultat (job annulé ou temps dépassé sur GitHub) : "
                                          "relance-la seule.", encoding="utf-8")
            out.append(sid)
    return out


def update_results_readme(results_dir: Path) -> Path:
    """results/README.md : liste des lots, le plus récent en premier (pratique depuis le téléphone)."""
    results_dir = Path(results_dir)
    lots = sorted((p for p in results_dir.iterdir() if p.is_dir() and (p / "INDEX.md").exists()), reverse=True)
    L = ["# Résultats", "", "Lots de recherches, du plus récent au plus ancien.", ""]
    for p in lots[:200]:
        first = (p / "INDEX.md").read_text(encoding="utf-8").splitlines()[0].lstrip("# ")
        L.append(f"- [{p.name}]({p.name}/INDEX.md) — {first}")
    out = results_dir / "README.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    return out


def main(argv=None) -> int:
    a = list(sys.argv[1:] if argv is None else argv)
    if len(a) >= 2 and a[0] == "matrix":
        text = a[1]
        if not text.strip() and len(a) >= 3 and Path(a[2]).exists():  # formulaire vide -> fichier recherches.txt
            text = Path(a[2]).read_text(encoding="utf-8")
        default_type = a[3] if len(a) >= 4 else "auto"
        print(json.dumps(parse(text, default_type), ensure_ascii=False))
        return 0
    if len(a) >= 3 and a[0] == "missing":
        print("\n".join(mark_missing(Path(a[1]), a[2])))
        return 0
    if len(a) >= 2 and a[0] == "merge":
        p = merge(Path(a[1]))
        update_results_readme(Path(a[1]).parent)
        print(p)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
