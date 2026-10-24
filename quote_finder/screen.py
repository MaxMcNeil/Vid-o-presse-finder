"""Tri des candidats AVANT l'analyse coûteuse : pertinence jugée par l'IA sur titre + description (1 seul appel).

Sans ce tri, une chaîne très sérieuse mais hors sujet (« sécurité routière » pour « la Suisse abandonne Microsoft »)
passait devant une vidéo pertinente. Pertinence d'abord, autorité ensuite.
"""
from __future__ import annotations

import logging

from . import scan
from .util import normalize

log = logging.getLogger("qf.screen")

PROMPT = """Tu fais le tri avant une recherche de déclarations.
CIBLE ({ttype}) : {target}
SUJET RECHERCHÉ : {claim}
(Reformulation : {summary})
{since}
Voici des {kind} trouvés par un moteur de recherche (beaucoup sont hors sujet). Pour CHACUN, note de 0 à 10 la probabilité
qu'il contienne une déclaration ou une analyse sur CE sujet précis à propos de CETTE cible :
10 = c'est exactement le sujet ; 6 = le sujet est probablement abordé ; 3 = même thème général mais autre affaire ;
0 = hors sujet (autre pays, autre affaire, autre époque sans rapport, divertissement, publicité).

{items}

Réponds en JSON : {{"scores": [{{"i": numéro, "s": note}}, ...]}} avec une entrée pour chaque numéro."""


def local_relevance(title: str, desc: str, plan, target_type: str) -> float:
    """Pertinence locale gratuite (0..~5) : concepts du sujet + nom de la cible dans le titre/la description."""
    meta = normalize(f"{title} {desc[:800]}")
    tmeta = normalize(title)
    g = scan.group_score(meta, plan.term_groups)
    gt = scan.group_score(tmeta, plan.term_groups)
    words = set(meta.split())
    stop = {"president", "ministre", "premier", "france", "monsieur", "madame", "the", "les", "des"}
    alias = any(a and len(a) > 2 and f" {a} " in f" {meta} " for a in plan.target_aliases) or \
        any(w in words for a in plan.target_aliases for w in a.split() if len(w) >= 4 and w not in stop)  # nom de famille seul
    return g + 0.5 * gt + (1.0 if alias or target_type == "sujet" else 0.0)


def llm_scores(llm, plan, o, items: list[tuple[str, str, str, str]], kind: str = "vidéos") -> dict[int, float] | None:
    """items = (titre, source, date, description). Renvoie {index: note 0..10} ou None si l'IA ne répond pas."""
    if not items or llm is None:
        return None
    lines = []
    for i, (title, src, date, desc) in enumerate(items, 1):
        d = " ".join((desc or "").split())[:180]
        lines.append(f"{i}. « {title} » — {src} — {date[:10] or '?'}" + (f" — {d}" if d else ""))
    prompt = PROMPT.format(ttype=plan.target_type, target=o.target, claim=o.claim or "(tout propos marquant sur la cible)",
                           summary=plan.claim_summary, kind=kind, items="\n".join(lines),
                           since=f"Période : à partir du {o.since}.\n" if getattr(o, "since", "") else "")
    try:
        d = llm.generate_json(prompt)
    except Exception as e:  # noqa: BLE001 - le tri est un plus : sans lui, on garde le tri local
        log.warning("Tri par l'IA indisponible (%s) : tri local seulement", str(e)[:100])
        return None
    out = {}
    for x in (d or {}).get("scores", []) or []:
        try:
            out[int(x["i"]) - 1] = float(x["s"])
        except (KeyError, TypeError, ValueError):
            continue
    return out or None
