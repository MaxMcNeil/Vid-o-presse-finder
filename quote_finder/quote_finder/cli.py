from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from . import report
from .llm import LLMRouter
from . import pipeline
from .pipeline import Options, PlanError, run
from .util import slugify


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="quote_finder", description="Trouve où et quand quelqu'un affirme quelque chose.")
    p.add_argument("--person", required=True, help="Personne dont on cherche les propos (ex. 'Bill Clinton')")
    p.add_argument("--claim", required=True, help="Sujet/affirmation : mot, phrase, thème... (ex. 'Saddam a des armes de destruction massive')")
    p.add_argument("--also", default="", help="Autres locuteurs acceptés, séparés par des virgules (ex. 'Colin Powell, Tony Blair')")
    p.add_argument("--anyone", action="store_true", help="Accepter n'importe quel locuteur qui fait cette affirmation")
    p.add_argument("--langs", default="en,fr", help="Langues des sous-titres/requêtes, ex. 'en,fr,es'")
    p.add_argument("--max-videos", type=int, default=30)
    p.add_argument("--max-articles", type=int, default=25)
    p.add_argument("--video-fallback-max", type=int, default=15, help="Vidéos sans transcription analysées directement par Gemini")
    p.add_argument("--audio-fallback-max", type=int, default=10, help="Vidéos sans sous-titres dont Gemini écoute l'audio")
    p.add_argument("--min-confidence", type=float, default=0.55)
    p.add_argument("--strict", action="store_true", help="Exclure les propos rapportés/locuteur incertain")
    p.add_argument("--platforms", default="youtube,dailymotion,archive")
    p.add_argument("--no-articles", action="store_true")
    p.add_argument("--max-minutes", type=int, default=40, help="durée maximale ; au-delà on rend les résultats partiels")
    p.add_argument("--allow-heuristic", action="store_true", help="continuer même si l'IA ne répond pas (déconseillé)")
    p.add_argument("--out", default="results")
    return p.parse_args(argv)


def main(argv=None) -> int:
    a = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    split = lambda s: [x.strip() for x in s.split(",") if x.strip()]  # noqa: E731
    o = Options(person=a.person.strip(), claim=a.claim.strip(), also=split(a.also), anyone=a.anyone,
                langs=split(a.langs) or ["en"], max_videos=a.max_videos, max_articles=a.max_articles,
                video_fallback_max=a.video_fallback_max, audio_fallback_max=a.audio_fallback_max, min_confidence=a.min_confidence, strict=a.strict,
                platforms=split(a.platforms), articles=not a.no_articles,
                max_minutes=a.max_minutes, allow_heuristic=a.allow_heuristic)
    llm = LLMRouter.from_env()
    if not llm.has_text:
        logging.warning("Aucune clé Gemini/LLM : impossible de juger le contenu. Définis GEMINI_API_KEYS.")
        return 2
    try:
        plan, vres, ares = run(o, llm)
    except PlanError as e:
        logging.error("%s", e)
        print(f"ÉCHEC : {e}")
        return 3
    out = Path(a.out) / slugify(f"{o.person} {o.claim}")
    stats = {"videos_scanned": len(vres), "articles_scanned": len(ares)}
    p = report.build(o.person, o.claim, plan, vres, ares, out, stats)
    print(f"Rapport : {p}")
    summ = os.environ.get("GITHUB_STEP_SUMMARY")
    if summ:
        with open(summ, "a", encoding="utf-8") as f:
            f.write(p.read_text(encoding="utf-8")[:900_000])
    if pipeline.TIMED_OUT.is_set():  # des tâches bloquées pourraient retenir le processus : on sort proprement
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
