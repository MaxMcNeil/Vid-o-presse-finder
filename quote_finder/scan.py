"""Pré-filtre local des transcriptions (gratuit) puis jugement par LLM sur les seuls passages pertinents."""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

try:
    from rapidfuzz import fuzz
    HAVE_RAPIDFUZZ = True
except ImportError:  # sans compilation : repli pur Python (difflib)
    fuzz = None
    HAVE_RAPIDFUZZ = False
import difflib
from functools import lru_cache

from .models import Hit, Plan, Segment
from .util import fmt_ts, normalize, parse_ts

log = logging.getLogger("qf.scan")

WIN, STEP, CONTEXT = 40.0, 20.0, 25.0


@lru_cache(maxsize=200_000)
def _tok_close(a: str, b: str) -> bool:
    if a == b:
        return True
    if len(a) < 5 or abs(len(a) - len(b)) > 2:
        return False
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.84


def _fuzzy_fallback(text_norm: str, term: str) -> bool:
    """Sans rapidfuzz : chaque mot du terme doit se retrouver (à une faute près) dans la fenêtre."""
    words = set(text_norm.split())
    return all(any(_tok_close(t, w) for w in words) for t in term.split())


def term_present(text_norm: str, term: str) -> bool:
    if not term:
        return False
    if f" {term} " in f" {text_norm} ":
        return True
    if len(term) < 5:
        return False
    # tolérance aux erreurs de transcription automatique ("sadam", "weapon of mass destruction"...)
    if not HAVE_RAPIDFUZZ:
        return _fuzzy_fallback(text_norm, term)
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



# ------------------------------------------------------------------ jugement par l'IA
TYPE_WORD = {"personne": "personne", "pays": "pays", "entite": "entité", "sujet": "sujet"}


def _context(plan: Plan, o) -> dict:
    ttype = plan.target_type or getattr(o, "target_type", "personne")
    official = ", ".join(plan.official_speakers) or "aucun connu"
    if getattr(o, "speakers", "cible") == "cible" and ttype != "sujet":
        who = (f"Seuls comptent les propos tenus PAR la cible elle-même"
               + ("" if ttype == "personne" else f" ou par ses représentants officiels ({official})")
               + ". who_says = \"target\" dans ce cas.")
    else:
        who = ("TOUT LOCUTEUR EST ACCEPTÉ (officiel, expert, journaliste, adversaire...) : la cible est le SUJET des propos, "
               "pas forcément celle qui parle ; renseigne toujours speaker_name et speaker_role. "
               f"Représentants officiels de la cible : {official} (who_says = \"target\" s'ils parlent).")
    return dict(target=o.target, ttype=TYPE_WORD.get(ttype, ttype), aliases=", ".join(plan.target_aliases[:12]),
                others=", ".join(getattr(o, "also", []) or []) or "aucun", who=who,
                claim=o.claim or "(tout propos marquant sur la cible)", summary=plan.claim_summary)


FIELDS = """- "speaker_name": nom du locuteur UNIQUEMENT s'il est cité dans le contenu, la description, le titre ou un bandeau ; n'invente jamais un nom, sinon ""
- "speaker_role": "official" (gouvernement, élu en fonction, porte-parole, dirigeant d'institution/entreprise) | "expert" (chercheur, spécialiste reconnu) | "politician" | "journalist" | "other"
- "confidence": nombre de 0 à 1
- "why": une phrase justifiant le jugement"""

JUDGE_PROMPT = """Tu vérifies, à partir d'extraits de transcription ({kind}) d'un contenu, si l'affirmation / le sujet recherché y est exprimé.

CIBLE ({ttype}) : {target}  (alias : {aliases})
AUTRES LOCUTEURS ACCEPTÉS : {others}
{who}
AFFIRMATION / SUJET RECHERCHÉ : {claim}
(Reformulation : {summary})

CONTENU : titre « {title} » — chaîne/source : {channel} — date : {date}
Description : {desc}
ATTENTION : le titre et la description peuvent être trompeurs ; juge uniquement d'après les extraits.

Les extraits sont horodatés. Les transcriptions automatiques n'indiquent PAS qui parle : déduis le locuteur du contexte
(présentation, questions/réponses, « je », « nous », « mon gouvernement », description). Si tu ne peux pas savoir, mets "unknown".

EXTRAITS :
{excerpts}

Pour chaque passage où l'affirmation est faite — à la lettre OU dans le sens — ajoute un élément à "hits".
Un même extrait peut contenir plusieurs occurrences (répétitions) : une entrée par occurrence, avec son propre horodatage.
Champs :
- "excerpt": numéro de l'extrait
- "t": horodatage en secondes du DÉBUT de la déclaration (lu sur les [mm:ss] de l'extrait)
- "t_end": horodatage en secondes de la FIN de la déclaration : le [mm:ss] de la ligne qui suit la dernière phrase concernée
  (si la déclaration va jusqu'au bout de l'extrait, estime-la). La déclaration doit être complète : début de la phrase jusqu'à sa conclusion
- "quote": citation EXACTE copiée de l'extrait, de la première à la dernière phrase de la déclaration (max 400 caractères)
- "verdict": "literal" (formulation quasi identique) | "paraphrase" (même sens, autres mots) | "mention" (le sujet est évoqué sans être affirmé) | "denial" (le locuteur nie/conteste)
- "who_says": "target" (la cible ou son représentant officiel parle) | "other_named" (un autre locuteur identifiable parle) | "reported" (un narrateur RAPPORTE les propos de quelqu'un) | "unknown"
{fields}
N'invente rien : si aucun passage ne convient, renvoie {{"hits": []}}.
Réponds en JSON : {{"hits": [...]}}"""


def _hit_from(d: dict) -> Hit | None:
    try:
        return Hit(t=parse_ts(d.get("t")), t_end=parse_ts(d.get("t_end")), quote=str(d.get("quote", "")).strip(),
                   verdict=str(d.get("verdict", "")).lower(), who_says=str(d.get("who_says", "unknown")).lower(),
                   speaker_name=str(d.get("speaker_name", "") or ""), confidence=float(d.get("confidence", 0) or 0),
                   why=str(d.get("why", "")), speaker_role=str(d.get("speaker_role", "") or "").lower())
    except (TypeError, ValueError):
        return None


def judge_excerpts(llm, plan: Plan, o, meta: dict, excerpts: list[Excerpt], kind: str = "vidéo") -> list[Hit]:
    if not excerpts:
        return []
    body = "\n\n".join(f"--- EXTRAIT {i + 1} ---\n{e.text}" for i, e in enumerate(excerpts))
    prompt = JUDGE_PROMPT.format(kind=kind, fields=FIELDS, title=meta.get("title", ""), channel=meta.get("channel", ""),
                                 date=meta.get("published", ""), desc=(meta.get("description", "") or "")[:500].replace("\n", " "),
                                 excerpts=body, **_context(plan, o))
    d = llm.generate_json(prompt)
    hits = [h for h in (_hit_from(x) for x in (d or {}).get("hits", [])) if h and h.quote]
    return dedupe_hits(hits)


VIDEO_PROMPT = """{medium}
CIBLE ({ttype}) : {target} (alias : {aliases}). AUTRES LOCUTEURS ACCEPTÉS : {others}.
{who}
AFFIRMATION / SUJET RECHERCHÉ : {claim}  (Reformulation : {summary})
Le titre peut être trompeur : juge uniquement ce qui est réellement dit.
Liste CHAQUE moment où l'affirmation est faite, à la lettre ou dans le sens, avec l'horodatage exact (début de la phrase).
Champs de chaque élément de "hits" : "t" (secondes depuis le début de la vidéo : DÉBUT de la déclaration), "t_end" (secondes : FIN de la
déclaration, juste après sa dernière phrase ; la déclaration doit être complète), "quote" (propos tenus, au plus près),
"verdict" ("literal"|"paraphrase"|"mention"|"denial"), "who_says" ("target"|"other_named"|"reported"|"unknown"),
{fields}
(pour speaker_name : nom prononcé ou affiché à l'écran, p. ex. bandeau ; sinon "Journaliste" ou "Intervenant").
Si rien ne correspond : {{"hits": []}}. Réponds en JSON : {{"hits": [...]}}"""


def judge_video_direct(llm, plan: Plan, o, url: str, audio: bytes | None = None) -> list[Hit]:
    medium = "Écoute cet enregistrement audio en entier." if audio else "Regarde et écoute cette vidéo en entier."
    prompt = VIDEO_PROMPT.format(medium=medium, fields=FIELDS, **_context(plan, o))
    if audio:
        d = llm.generate_json(prompt, inline_media=("audio/mp3", audio), media_key=url)
    else:
        d = llm.generate_json(prompt, video_url=url)
    return dedupe_hits([h for h in (_hit_from(x) for x in (d or {}).get("hits", [])) if h and h.quote])


def dedupe_hits(hits: list[Hit], gap: float = 8.0) -> list[Hit]:
    out: list[Hit] = []
    for h in sorted(hits, key=lambda x: (x.t if x.t is not None else -1)):
        if out and h.t is not None and out[-1].t is not None and abs(h.t - out[-1].t) < gap:
            ends = [e for e in (h.t_end, out[-1].t_end) if e is not None]
            if h.confidence > out[-1].confidence:
                out[-1] = h
            if ends:
                out[-1].t_end = max(ends)  # on garde l'étendue complète des deux détections
            continue
        out.append(h)
    return out


# ------------------------------------------------------------------ filtrage / classement
def _names(plan: Plan, others: list[str]) -> list[str]:
    return [a for a in plan.target_aliases + [normalize(x) for x in plan.official_speakers + list(others or [])] if a]


def speaker_ok(h: Hit, plan: Plan, o) -> bool:
    """Le bon locuteur parle-t-il ? (mode « cible » : la cible ou son représentant officiel)"""
    if getattr(o, "speakers", "cible") != "cible" or plan.target_type == "sujet":
        return True
    if h.who_says == "target":
        return True
    name = normalize(h.speaker_name)
    if name and any(a in name or name in a for a in _names(plan, o.also)):
        return True
    return h.who_says in ("reported", "unknown")  # gardés mais étiquetés (voir rapport) ; --strict les exclut


def keep_hit(h: Hit, plan: Plan, o) -> bool:
    if h.verdict not in ("literal", "paraphrase") or h.confidence < o.min_confidence:
        return False
    if not speaker_ok(h, plan, o):
        return False
    if o.strict:
        if h.who_says in ("reported", "unknown"):
            return False
    return True


ROLE_BONUS = {"official": 0.15, "expert": 0.1, "politician": 0.05, "journalist": 0.0, "other": -0.1}


def hit_score(h: Hit, source_authority: float = 0.5) -> float:
    """Score global pour le montage : sûreté du passage + autorité de la source + rôle du locuteur."""
    s = 0.45 * h.confidence + 0.4 * source_authority + ROLE_BONUS.get(h.speaker_role, 0.0)
    s += 0.1 if h.verdict == "literal" else 0.0
    s -= 0.15 if h.who_says in ("reported", "unknown") else 0.0
    return round(max(0.0, min(1.0, s)), 3)


def rank_key(h: Hit, source_authority: float = 0.5):
    return (-hit_score(h, source_authority),)


# ------------------------------------------------------------------ horodatage précis
def _pr(a: str, b: str) -> float:
    """Similarité partielle 0-100 (a contenu dans b, à quelques fautes près)."""
    if not a or not b:
        return 0.0
    if a in b:
        return 100.0
    if HAVE_RAPIDFUZZ:
        return float(fuzz.partial_ratio(a, b))
    aw, bw = a.split(), b.split()
    n = len(aw)
    best = 0.0
    for i in range(max(1, len(bw) - n + 1)):
        best = max(best, difflib.SequenceMatcher(None, a, " ".join(bw[i:i + n])).ratio() * 100)
    return best


def refine_times(hits: list[Hit], segs: list[Segment], radius: float = 90.0) -> None:
    """Cale début et fin sur les lignes de la transcription qui contiennent vraiment la citation (±1-2 s),
    au lieu de l'horodatage lu par l'IA. Sans correspondance sûre, l'horodatage IA est conservé."""
    if not segs:
        return
    norm = [normalize(s.text) for s in segs]
    for h in hits:
        if h.t is None:
            continue
        words = normalize(h.quote).split()
        if len(words) < 3:
            continue
        head, tail = " ".join(words[:6]), " ".join(words[-6:])
        first = " ".join(words[:2])
        idx = [i for i, s in enumerate(segs) if abs(s.start - h.t) <= radius]
        if not idx:
            continue
        best_i, best = None, 0.0
        for i in idx:
            # la déclaration doit COMMENCER dans cette ligne : son 1er mot y figure (à une faute près)
            if not term_present(norm[i], words[0]) and not (len(words[0]) < 5 and words[0] in norm[i].split()):
                continue
            if _pr(first, norm[i] + " " + (norm[i + 1] if i + 1 < len(norm) else "")) < 80:
                continue
            sc = _pr(head, " ".join(norm[i:i + 3]))
            closer = best_i is not None and abs(sc - best) <= 0.5 and abs(segs[i].start - h.t) < abs(segs[best_i].start - h.t)
            if sc > best + 0.5 or closer:
                best_i, best = i, sc
        if best_i is None or best < 82:
            continue
        end_j, best_e = None, 0.0
        for j in range(best_i, min(len(segs), best_i + 60)):
            if segs[j].start - segs[best_i].start > 240:
                break
            win = " ".join(norm[max(best_i, j - 2):j + 1])
            # la déclaration doit FINIR dans cette ligne : son dernier mot y figure
            if not term_present(norm[j], words[-1]) and not (len(words[-1]) < 5 and words[-1] in norm[j].split()):
                continue
            sc = _pr(tail, win)
            if sc >= best_e:
                end_j, best_e = j, sc
            if sc >= 99:
                break
        h.t = segs[best_i].start
        if end_j is not None and best_e >= 80:
            nxt = segs[end_j + 1].start if end_j + 1 < len(segs) else segs[end_j].start + 4.0
            h.t_end = max(nxt, h.t + 1.0)
        h.timing = "sous-titres"


def finish_times(hits: list[Hit], duration: float | None = None) -> None:
    """Garantit un intervalle [début, fin] exploitable : fin estimée d'après la longueur de la citation si besoin.
    Un passage qui commencerait après la fin de la vidéo est un horodatage inventé par l'IA : il est retiré."""
    if duration:
        bad = [h for h in hits if h.t is not None and h.t >= float(duration) - 1]
        if bad:
            log.info("%d passage(s) horodaté(s) après la fin de la vidéo (%ss) : retirés", len(bad), duration)
        hits[:] = [h for h in hits if h not in bad]
    for h in hits:
        if h.t is None:
            continue
        est = max(4.0, len(h.quote.split()) / 2.5 + 1.5)
        if h.t_end is None or h.t_end <= h.t or h.t_end - h.t > 240 or (h.timing != "sous-titres" and h.t_end - h.t < 0.6 * est):
            h.t_end = h.t + est
        if duration:
            h.t_end = min(h.t_end, float(duration))
        if h.t_end <= h.t:  # garde-fou final : jamais de durée nulle ou négative
            h.t_end = h.t + est
