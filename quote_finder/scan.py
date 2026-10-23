"""Pré-filtre local des transcriptions (gratuit) puis jugement par LLM sur les seuls passages pertinents."""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from rapidfuzz import fuzz

from .models import Hit, Plan, Segment
from .util import fmt_ts, normalize, parse_ts

log = logging.getLogger("qf.scan")

WIN, STEP, CONTEXT = 40.0, 20.0, 25.0


def term_present(text_norm: str, term: str) -> bool:
    if not term:
        return False
    if f" {term} " in f" {text_norm} ":
        return True
    if len(term) < 5:
        return False
    # tolérance aux erreurs de transcription automatique ("sadam", "weapon of mass destruction"...)
    thr = 90 if len(term) < 9 else 85
    return fuzz.partial_ratio(term, text_norm) >= thr


def group_score(text: str, groups: list[list[str]]) -> int:
    n = normalize(text)
    return sum(1 for g in groups if any(term_present(n, t) for t in g))


def needed(groups: list[list[str]]) -> int:
    return max(1, math.ceil(0.6 * len(groups)))


@dataclass
class Excerpt:
    start: float
    end: float
    score: int
    text: str  # lignes "[mm:ss] texte"


def find_excerpts(segs: list[Segment], plan: Plan, max_excerpts: int = 8, max_chars: int = 3500,
                  win: float = WIN, step: float = STEP, context: float = CONTEXT, merge_gap: float = 15) -> list[Excerpt]:
    if not segs:
        return []
    need = needed(plan.term_groups)
    end_t = segs[-1].start
    hits: list[tuple[float, float, int]] = []
    t = 0.0
    j0 = 0
    while t <= end_t:
        while j0 < len(segs) and segs[j0].start < t:
            j0 += 1
        j = j0
        buf = []
        while j < len(segs) and segs[j].start < t + win:
            buf.append(segs[j].text)
            j += 1
        if buf:
            sc = group_score(" ".join(buf), plan.term_groups)
            if sc >= need:
                hits.append((t, t + win, sc))
        t += step
    # fusion des fenêtres voisines
    merged: list[list[float]] = []
    for a, b, sc in hits:
        if merged and a <= merged[-1][1] + merge_gap:
            merged[-1][1] = max(merged[-1][1], b)
            merged[-1][2] = max(merged[-1][2], sc)
        else:
            merged.append([a, b, sc])
    merged.sort(key=lambda m: (-m[2], m[0]))
    out = []
    for a, b, sc in merged[:max_excerpts]:
        lo, hi = a - context, b + context
        lines = [f"[{fmt_ts(s.start)}] {s.text}" for s in segs if lo <= s.start <= hi]
        text = "\n".join(lines)
        if len(text) > max_chars:  # très longue séquence : on tronque autour du début
            text = text[:max_chars]
        out.append(Excerpt(a, b, int(sc), text))
    out.sort(key=lambda e: e.start)
    return out


JUDGE_PROMPT = """Tu vérifies si une personne AFFIRME quelque chose, à partir d'extraits de transcription ({kind}) d'un contenu.

PERSONNE CIBLE : {person}  (alias : {aliases})
AUTRES LOCUTEURS ACCEPTÉS : {others}
{anyone_line}AFFIRMATION RECHERCHÉE : {claim}
(Reformulation : {summary})

CONTENU : titre « {title} » — chaîne/source : {channel} — date : {date}
Description : {desc}
ATTENTION : le titre et la description peuvent être trompeurs ; juge uniquement d'après les extraits.

Les extraits sont horodatés. Les transcriptions automatiques n'indiquent PAS qui parle : déduis le locuteur du contexte
(présentation, questions/réponses, « je », « nous », « mon administration », description). Si tu ne peux pas savoir, mets "unknown".

EXTRAITS :
{excerpts}

Pour chaque passage où l'affirmation est faite — à la lettre OU dans le sens — ajoute un élément à "hits".
Un même extrait peut contenir plusieurs occurrences (répétitions) : une entrée par occurrence, avec son propre horodatage.
Champs :
- "excerpt": numéro de l'extrait
- "t": horodatage en secondes du début de la phrase concernée (lu sur les [mm:ss] de l'extrait)
- "quote": citation EXACTE copiée de l'extrait (max 300 caractères)
- "verdict": "literal" (formulation quasi identique) | "paraphrase" (même sens, autres mots) | "mention" (le sujet est évoqué mais sans affirmer) | "denial" (la personne nie/conteste l'affirmation)
- "who_says": "target" (la personne cible parle elle-même) | "other_named" (un autre locuteur identifiable parle) | "reported" (un narrateur/journaliste RAPPORTE que quelqu'un a dit cela) | "unknown"
- "speaker_name": nom du locuteur si identifiable, sinon ""
- "confidence": nombre de 0 à 1
- "why": une phrase justifiant le jugement
N'invente rien : si aucun passage ne convient, renvoie {{"hits": []}}.
Réponds en JSON : {{"hits": [...]}}"""


def _hit_from(d: dict, offset_ok=True) -> Hit | None:
    try:
        t = parse_ts(d.get("t"))
        return Hit(t=t, quote=str(d.get("quote", "")).strip(), verdict=str(d.get("verdict", "")).lower(),
                   who_says=str(d.get("who_says", "unknown")).lower(), speaker_name=str(d.get("speaker_name", "") or ""),
                   confidence=float(d.get("confidence", 0) or 0), why=str(d.get("why", "")))
    except (TypeError, ValueError):
        return None


ANYONE = ("TOUT LOCUTEUR EST ACCEPTÉ : relève l'affirmation quel que soit celui qui la fait (journaliste, adversaire, "
          "invité...). La « personne cible » est alors le SUJET de l'affirmation, pas forcément celui qui parle ; "
          "renseigne toujours speaker_name.\n")


def judge_excerpts(llm, plan: Plan, person: str, others: list[str], claim: str, meta: dict,
                   excerpts: list[Excerpt], kind: str = "vidéo", anyone: bool = False) -> list[Hit]:
    if not excerpts:
        return []
    body = "\n\n".join(f"--- EXTRAIT {i + 1} ---\n{e.text}" for i, e in enumerate(excerpts))
    prompt = JUDGE_PROMPT.format(
        kind=kind, person=person, aliases=", ".join(plan.person_aliases), others=", ".join(others) or "aucun",
        claim=claim, anyone_line=ANYONE if anyone else "", summary=plan.claim_summary, title=meta.get("title", ""), channel=meta.get("channel", ""),
        date=meta.get("published", ""), desc=(meta.get("description", "") or "")[:500].replace("\n", " "), excerpts=body)
    d = llm.generate_json(prompt)
    hits = [h for h in (_hit_from(x) for x in (d or {}).get("hits", [])) if h and h.quote]
    return dedupe_hits(hits)


VIDEO_PROMPT = """{medium}
PERSONNE CIBLE : {person} (alias : {aliases}). AUTRES LOCUTEURS ACCEPTÉS : {others}.
{anyone_line}AFFIRMATION RECHERCHÉE : {claim}  (Reformulation : {summary})
Le titre peut être trompeur : juge uniquement ce qui est réellement dit.
Liste CHAQUE moment où l'affirmation est faite, à la lettre ou dans le sens, avec l'horodatage exact (début de la phrase).
Champs de chaque élément de "hits" : "t" (secondes depuis le début de la vidéo), "quote" (propos tenus, au plus près),
"verdict" ("literal"|"paraphrase"|"mention"|"denial"), "who_says" ("target"|"other_named"|"reported"|"unknown"),
"speaker_name", "confidence" (0-1), "why".
Si rien ne correspond : {{"hits": []}}. Réponds en JSON : {{"hits": [...]}}"""


def judge_video_direct(llm, plan: Plan, person: str, others: list[str], claim: str, url: str,
                       audio: bytes | None = None, anyone: bool = False) -> list[Hit]:
    medium = ("Écoute cet enregistrement audio en entier." if audio
              else "Regarde et écoute cette vidéo en entier.")
    prompt = VIDEO_PROMPT.format(medium=medium, person=person, aliases=", ".join(plan.person_aliases),
                                 others=", ".join(others) or "aucun", claim=claim, summary=plan.claim_summary,
                                 anyone_line=ANYONE if anyone else "")
    if audio:
        d = llm.generate_json(prompt, inline_media=("audio/mp3", audio), media_key=url)
    else:
        d = llm.generate_json(prompt, video_url=url)
    return dedupe_hits([h for h in (_hit_from(x) for x in (d or {}).get("hits", [])) if h and h.quote])


def dedupe_hits(hits: list[Hit], gap: float = 8.0) -> list[Hit]:
    out: list[Hit] = []
    for h in sorted(hits, key=lambda x: (x.t if x.t is not None else -1)):
        if out and h.t is not None and out[-1].t is not None and abs(h.t - out[-1].t) < gap:
            if h.confidence > out[-1].confidence:
                out[-1] = h
            continue
        out.append(h)
    return out


def speaker_ok(h: Hit, plan: Plan, anyone: bool, others: list[str]) -> bool:
    """La personne voulue (ou un locuteur autorisé) est-elle bien celle qui affirme ?"""
    if anyone:
        return True
    if h.who_says == "target":
        return True
    name = normalize(h.speaker_name)
    if name:
        for a in plan.person_aliases + [normalize(o) for o in others]:
            if a and (a in name or name in a):
                return True
    return h.who_says == "reported" or h.who_says == "unknown"  # gardés mais étiquetés (voir rapport)


def keep_hit(h: Hit, plan: Plan, anyone: bool, others: list[str], min_conf: float, strict: bool) -> bool:
    if h.verdict not in ("literal", "paraphrase") or h.confidence < min_conf:
        return False
    if not speaker_ok(h, plan, anyone, others):
        return False
    if strict and not anyone and h.who_says not in ("target",) and not any(
            a and a in normalize(h.speaker_name) for a in plan.person_aliases + [normalize(o) for o in others]):
        return False
    return True


def rank_key(h: Hit):
    own = 0 if h.who_says in ("target", "other_named") else 1
    lit = 0 if h.verdict == "literal" else 1
    return (own, lit, -h.confidence)
