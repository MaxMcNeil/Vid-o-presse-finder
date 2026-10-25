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
from .util import fmt_ts, items_of, normalize, parse_ts

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


RULES = """RÈGLES (le résultat sert à un montage vidéo : chaque passage doit être exact, complet et pile sur le sujet) :
- La citation doit, À ELLE SEULE, porter sur la CIBLE et sur le SUJET. Si elle concerne une autre cible ou une autre région
  (ex. l'Afrique seule alors que la cible est l'Europe) ou une autre époque/affaire, verdict = "mention".
- UN passage = UN moment continu de 5 à 40 s environ. N'assemble pas avec « ... » des phrases éloignées : fais plusieurs éléments.
- Ne sont PAS des déclarations : une question posée par le présentateur, un titre ou bandeau affiché à l'écran,
  le lancement d'un sujet (« Could this ...? ») ou la signature de fin de reportage (« X, Al Jazeera, Abuja »).
- Ne liste pas deux fois le même passage (pas de chevauchement entre deux éléments).
"""

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
{rules}
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


# noms qui n'en sont pas (renvoyés parfois par l'IA à la place d'un vrai nom)
NOT_A_NAME = {"target", "cible", "unknown", "inconnu", "inconnue", "journaliste", "journalist", "intervenant", "intervenante",
              "speaker", "locuteur", "narrateur", "narrator", "voix off", "voiceover", "voice over", "presentateur",
              "presentatrice", "presenter", "anchor", "reporter", "correspondant", "correspondent", "host", "n a", "na",
              "none", "null", "aucun", "other", "other named", "official", "expert", "unidentified", "non identifie"}


def clean_name(name: str) -> str:
    n = " ".join(str(name or "").split())
    return "" if normalize(n) in NOT_A_NAME or len(normalize(n)) < 2 else n


def _hit_from(d: dict) -> Hit | None:
    try:
        return Hit(t=parse_ts(d.get("t")), t_end=parse_ts(d.get("t_end")), quote=str(d.get("quote", "")).strip(),
                   verdict=str(d.get("verdict", "")).lower(), who_says=str(d.get("who_says", "unknown")).lower(),
                   speaker_name=clean_name(d.get("speaker_name", "")), confidence=float(d.get("confidence", 0) or 0),
                   why=str(d.get("why", "")), speaker_role=str(d.get("speaker_role", "") or "").lower())
    except (TypeError, ValueError):
        return None


def judge_excerpts(llm, plan: Plan, o, meta: dict, excerpts: list[Excerpt], kind: str = "vidéo") -> list[Hit]:
    if not excerpts:
        return []
    body = "\n\n".join(f"--- EXTRAIT {i + 1} ---\n{e.text}" for i, e in enumerate(excerpts))
    prompt = JUDGE_PROMPT.format(kind=kind, fields=FIELDS, rules=RULES, title=meta.get("title", ""), channel=meta.get("channel", ""),
                                 date=meta.get("published", ""), desc=(meta.get("description", "") or "")[:500].replace("\n", " "),
                                 excerpts=body, **_context(plan, o))
    d = llm.generate_json(prompt)
    hits = [h for h in (_hit_from(x) for x in items_of(d, "hits")) if h and h.quote]
    return dedupe_hits(hits)


VIDEO_PROMPT = """{medium}
CIBLE ({ttype}) : {target} (alias : {aliases}). AUTRES LOCUTEURS ACCEPTÉS : {others}.
{who}
AFFIRMATION / SUJET RECHERCHÉ : {claim}  (Reformulation : {summary})
Le titre peut être trompeur : juge uniquement ce qui est réellement dit.
Liste CHAQUE moment où l'affirmation est faite, à la lettre ou dans le sens, avec l'horodatage exact (début de la phrase).
{rules}
Champs de chaque élément de "hits" : "t" (secondes depuis le début de la vidéo : DÉBUT de la déclaration), "t_end" (secondes : FIN de la
déclaration, juste après sa dernière phrase ; la déclaration doit être complète), "quote" (propos tenus, au plus près),
"verdict" ("literal"|"paraphrase"|"mention"|"denial"), "who_says" ("target"|"other_named"|"reported"|"unknown"),
{fields}
(pour speaker_name : nom prononcé ou affiché à l'écran, p. ex. bandeau ; sinon "" — speaker_role dit alors qui parle).
Si rien ne correspond : {{"hits": []}}. Réponds en JSON : {{"hits": [...]}}"""


def judge_video_direct(llm, plan: Plan, o, url: str, audio: bytes | None = None) -> list[Hit]:
    medium = "Écoute cet enregistrement audio en entier." if audio else "Regarde et écoute cette vidéo en entier."
    prompt = VIDEO_PROMPT.format(medium=medium, fields=FIELDS, rules=RULES, **_context(plan, o))
    if audio:
        d = llm.generate_json(prompt, inline_media=("audio/mp3", audio), media_key=url)
    else:
        d = llm.generate_json(prompt, video_url=url)
    return dedupe_hits([h for h in (_hit_from(x) for x in items_of(d, "hits")) if h and h.quote])


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


def _start_candidates(norm: list[str], words: list[str], idx: list[int], strict: bool) -> list[tuple[float, int]]:
    """(score, i) des lignes où la déclaration peut COMMENCER."""
    head, first = " ".join(words[:6]), " ".join(words[:2])
    out = []
    for i in idx:
        nxt = norm[i + 1] if i + 1 < len(norm) else ""
        if strict:
            # 1er mot dans la ligne (à une faute près) et 2 premiers mots à cheval sur cette ligne et la suivante
            if not term_present(norm[i], words[0]) and not (len(words[0]) < 5 and words[0] in norm[i].split()):
                continue
            if _pr(first, norm[i] + " " + nxt) < 80:
                continue
        else:
            # sous-titres automatiques approximatifs : un des 3 premiers mots suffit pour ancrer le début
            if not any(term_present(norm[i], w) or w in norm[i].split() for w in words[:3]):
                continue
        out.append((_pr(head, " ".join(norm[i:i + 3])), i))
    return out


def refine_times(hits: list[Hit], segs: list[Segment], radius: float = 90.0) -> None:
    """Cale début et fin sur les lignes de la transcription qui contiennent vraiment la citation (±1-2 s),
    au lieu de l'horodatage lu par l'IA. Recherche autour de l'horodatage IA, puis dans toute la vidéo
    (l'IA se trompe parfois de plusieurs minutes). Sans correspondance sûre, l'horodatage IA est conservé."""
    import re as _re
    if not segs:
        return
    norm = [normalize(s.text) for s in segs]
    every = list(range(len(segs)))
    for h in hits:
        if h.t is None:
            continue
        # citation avec coupure (« ... », « [...] ») : début cherché sur le 1er morceau, fin sur le dernier
        parts = [p for p in _re.split(r"\s*(?:\[\s*(?:\.\.\.|…)\s*\]|\.\.\.|…)\s*", h.quote) if len(normalize(p).split()) >= 3]
        words = normalize(parts[0] if parts else h.quote).split()
        last_words = normalize(parts[-1] if parts else h.quote).split()
        if len(words) < 3:
            continue
        tail = " ".join(last_words[-6:])
        near = [i for i, s in enumerate(segs) if abs(s.start - h.t) <= radius]
        best_i = None
        for idx, strict, need in ((near, True, 82), (near, False, 88), (every, True, 90), (every, False, 93)):
            cands = sorted(_start_candidates(norm, words, idx, strict), key=lambda x: (-x[0], abs(segs[x[1]].start - h.t)))
            if cands and cands[0][0] >= need:
                if idx is every and len(cands) > 1 and cands[1][0] >= cands[0][0] - 1 and \
                        abs(segs[cands[1][1]].start - segs[cands[0][1]].start) > 30:
                    continue  # phrase répétée à plusieurs endroits : ambigu, on ne devine pas
                best_i = cands[0][1]
                break
        if best_i is None:
            continue
        end_j, best_e = None, 0.0
        for j in range(best_i, min(len(segs), best_i + 80)):
            if segs[j].start - segs[best_i].start > 300:
                break
            win = " ".join(norm[max(best_i, j - 2):j + 1])
            if not term_present(norm[j], last_words[-1]) and not (len(last_words[-1]) < 5 and last_words[-1] in norm[j].split()):
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
        elif h.t_end is not None and h.t_end <= h.t:
            h.t_end = None  # fin inconnue : estimée ensuite d'après la longueur de la citation
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


# ------------------------------------------------------------------ nettoyage final (avant rapport et montage)
import re as _re2

_ELLIPSIS = _re2.compile(r"\s*(?:\[\s*(?:\.\.\.|…)\s*\]|\.\.\.|…)\s*")
_SIGNOFF = _re2.compile(r"(?:^|[.!?]\s+)([A-ZÀ-Ý][\w'’-]+(?:\s+[A-ZÀ-Ý][\w'’-]+){0,3}),\s*(?:al jazeera|reuters|bbc|euronews|"
                        r"france ?24|dw|cnn|sky news|rfi|afp|associated press|ap|rts|rai|tg24)\b[^.!?]*[.!?]?\s*$", _re2.I)


def _words(t: str) -> int:
    return len(normalize(t).split())


def est_duration(quote: str) -> float:
    """Durée parlée probable d'une citation (≈ 2,5 mots / s)."""
    return max(4.0, _words(quote) / 2.5 + 1.5)


def not_a_statement(h: Hit) -> str:
    """Raison d'écarter un passage qui n'est pas une déclaration, ou "" s'il est bon."""
    q = h.quote.strip()
    letters = [c for c in q if c.isalpha()]
    if _words(q) < 3:
        return "trop court"
    if len(letters) >= 8:  # écriture inutilisable pour le montage (ex. gujarati) : seuls latin et arabe sont gardés
        ok = sum(1 for c in letters if ord(c) < 0x250 or 0x600 <= ord(c) <= 0x6FF or 0x750 <= ord(c) <= 0x77F
                 or 0xFB50 <= ord(c) <= 0xFEFF)
        if ok / len(letters) < 0.6:
            return "langue non prise en charge"
    if len(letters) >= 8 and sum(c.isupper() for c in letters) / len(letters) > 0.8:
        return "titre affiché à l'écran"
    if q.rstrip(" »\"'").endswith("?") and h.speaker_role not in ("official", "expert", "politician"):
        return "question du présentateur"
    if _SIGNOFF.fullmatch(q) or (_SIGNOFF.search(q) and _words(_SIGNOFF.sub("", q)) < 5):
        return "signature de reportage"
    return ""


def tidy_hits(hits: list[Hit], duration: float | None = None, authority: float = 0.5) -> list[Hit]:
    """Garde-fous avant le montage : pas de faux passages (questions, bandeaux, signatures), pas de durée aberrante,
    jamais deux extraits qui se chevauchent dans la même vidéo."""
    out = []
    for h in hits:
        why = not_a_statement(h)
        if why:
            log.info("  écarté (%s) : %s", why, h.quote[:80])
            continue
        m = _SIGNOFF.search(h.quote)
        if m:  # signature en fin de citation : retirée du texte
            h.quote = h.quote[:m.start(0)].rstrip(" ,") + ("." if not h.quote[:m.start(0)].rstrip().endswith((".", "!", "?")) else "")
        if h.t is not None and h.t_end is not None:
            d, est = h.t_end - h.t, est_duration(h.quote)
            parts = [x for x in _ELLIPSIS.split(h.quote) if _words(x) >= 3]
            if len(parts) > 1 and d > 1.5 * est + 10:
                # morceaux éloignés collés avec « ... » : on garde le 1er morceau, seul horodaté avec certitude
                h.quote, h.t_end = parts[0].strip() + " …", h.t + est_duration(parts[0]) * 1.3
            elif d > 2 * est + 10:
                h.t_end = h.t + est * 1.3  # fin aberrante (plusieurs minutes pour une phrase) : ramenée à la durée parlée
            if duration:
                h.t_end = min(h.t_end, float(duration))
        out.append(h)
    # chevauchements dans une même vidéo : le meilleur est gardé ; petit chevauchement = fin du 1er avancée
    out.sort(key=lambda x: (x.t is None, x.t or 0))
    res: list[Hit] = []
    for h in out:
        prev = res[-1] if res else None
        if prev and h.t is not None and prev.t is not None and prev.t_end is not None and h.t < prev.t_end - 0.3:
            ov = min(prev.t_end, h.t_end or h.t) - h.t
            shorter = min(prev.t_end - prev.t, (h.t_end or h.t + 4) - h.t)
            if ov >= 0.3 * max(1.0, shorter):
                if hit_score(h, authority) > hit_score(prev, authority):
                    res[-1] = h
                continue
            prev.t_end = h.t
        res.append(h)
    return res
