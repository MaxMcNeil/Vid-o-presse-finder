"""2e passage : horodatage précis des passages trouvés par l'IA dans une vidéo YouTube sans sous-titres.

Depuis GitHub, YouTube bloque sous-titres ET téléchargements (« Sign in to confirm you're not a bot »), mais Gemini lit
la vidéo chez Google. On lui fait donc réécouter UNIQUEMENT une fenêtre autour du passage (video_metadata) et
transcrire chaque phrase avec début et fin au dixième de seconde. Effets :
  - horodatage affiné (± 1 s au lieu de ± 5 s) -> timing = "ia-affiné" ;
  - contrôle : si la citation n'est pas retrouvée (fenêtre étroite puis large), le passage est marqué
    timing = "non retrouvé" et exclu du montage (horodatage ou citation probablement inventés).
Testé le 24/10/2026 depuis GitHub sur 3 vidéos : seul ce moyen passe le blocage YouTube.
"""
from __future__ import annotations

import logging

from . import scan
from .util import normalize

log = logging.getLogger("qf.precise")

PROMPT = """Ce segment va de {a} s à {b} s depuis le DÉBUT DE LA VIDÉO.
Transcris mot à mot ce qui est dit, phrase par phrase, avec pour chaque phrase l'horodatage de début et de fin en secondes
depuis le DÉBUT DE LA VIDÉO (pas depuis le début du segment), au dixième de seconde.
Puis repère le passage qui correspond à cette citation (mêmes mots ou très proches, éventuellement traduite) :
« {quote} »
Donne le début de sa première phrase et la fin de sa dernière phrase. S'il n'est pas dans le segment, mets null.
JSON : {{"sentences": [{{"start": s, "end": s, "text": "..."}}], "quote_start": s ou null, "quote_end": s ou null}}"""


def _abs(x, a: float, b: float):
    """Gemini répond parfois en temps relatif au segment : on le ramène au temps de la vidéo."""
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    if a - 3 <= x <= b + 3:
        return x
    if 0 <= x <= (b - a) + 3:
        return a + x
    return None


def _match_in_sentences(sentences, quote: str, a: float, b: float):
    """Contrôle indépendant de la réponse de l'IA : retrouver la citation dans la transcription renvoyée."""
    words = normalize(quote).split()
    if len(words) < 3 or not sentences:
        return None
    head, tail = " ".join(words[:6]), " ".join(words[-6:])
    norm = [(s, normalize(str(s.get("text", "")))) for s in sentences if isinstance(s, dict)]
    st = next((s for s, n in norm if scan._pr(head, n) >= 85 or scan._pr(n[:60], head) >= 85), None)
    if not st:
        return None
    after = norm[norm.index(next(x for x in norm if x[0] is st)):]
    en = next((s for s, n in reversed(after) if scan._pr(tail, n) >= 85), st)
    t0, t1 = _abs(st.get("start"), a, b), _abs(en.get("end"), a, b)
    return (t0, t1) if t0 is not None and t1 is not None and t1 > t0 else None


def refine_hit(llm, url: str, h, duration: float | None) -> str:
    """Affine h.t / h.t_end. Renvoie le nouvel état de timing."""
    for pad in (25, 90):
        a = max(0.0, (h.t or 0) - pad)
        b = (h.t_end or (h.t or 0) + 10) + pad
        if duration:
            b = min(b, float(duration))
        if b - a < 5:
            continue
        try:
            d = llm.generate_json(PROMPT.format(a=int(a), b=int(b), quote=h.quote[:400]), video_url=url, video_clip=(a, b))
        except Exception as e:  # noqa: BLE001 - on garde l'horodatage de la 1re analyse
            log.info("affinage impossible (%s) : %s", url[-11:], str(e)[:100])
            return h.timing
        if isinstance(d, list) and d and isinstance(d[0], dict):
            d = d[0]
        if not isinstance(d, dict):
            continue
        found = _match_in_sentences(d.get("sentences") or [], h.quote, a, b)
        if found is None:
            qs, qe = _abs(d.get("quote_start"), a, b), _abs(d.get("quote_end"), a, b)
            if qs is not None and qe is not None and qe > qs:
                found = (qs, qe)
        if found:
            old = h.t
            h.t, h.t_end = round(found[0], 1), round(found[1], 1)
            h.timing = "ia-affiné"
            log.info("affiné %s : %.0fs → %.1fs-%.1fs", url[-11:], old or 0, h.t, h.t_end)
            return h.timing
    h.timing = "non retrouvé"
    log.info("passage non retrouvé à la réécoute (%s, %.0fs) : exclu du montage", url[-11:], h.t or 0)
    return h.timing
