from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys
import traceback
from pathlib import Path

from . import batch, pipeline, report
from .llm import LLMRouter
from .pipeline import Options, PlanError, run
from .util import slugify

TYPES = ["auto", "personne", "pays", "entite", "sujet"]


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="quote_finder",
                                description="Trouve où et quand une personne, un pays, une entité ou un sujet fait l'objet d'une déclaration.")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--target", "--person", dest="target", help="Cible : personne, pays, entité ou sujet (ex. 'La Suisse')")
    g.add_argument("--batch", help="Plusieurs recherches : 'cible | sujet | type ; cible | sujet ; ...'")
    g.add_argument("--batch-file", help="Fichier texte : une recherche par ligne 'cible | sujet | type'")
    p.add_argument("--claim", default="", help="Sujet / affirmation précise (facultatif)")
    p.add_argument("--type", dest="target_type", default="auto", choices=TYPES, help="Type de la cible (défaut : deviné)")
    p.add_argument("--speakers", default="auto", choices=["auto", "cible", "tous"],
                   help="cible = propos de la cible (ou de ses représentants) ; tous = toute voix qui fait autorité")
    p.add_argument("--also", default="", help="Autres locuteurs acceptés, séparés par des virgules")
    p.add_argument("--anyone", action="store_true", help="(ancien) équivaut à --speakers tous")
    p.add_argument("--langs", default="fr,en", help="Langues des sous-titres/requêtes, ex. 'fr,en,de'")
    p.add_argument("--max-videos", type=int, default=8, help="Vidéos analysées par recherche (les plus solides)")
    p.add_argument("--max-articles", type=int, default=6, help="Articles retenus par recherche")
    p.add_argument("--authority", default="forte", choices=["forte", "moyenne", "toutes"],
                   help="Exigence sur la source : forte = agences, grands médias, officiels")
    p.add_argument("--since", default="", help="Ignorer ce qui est antérieur à cette date (AAAA-MM-JJ)")
    p.add_argument("--video-fallback-max", type=int, default=None, help="Vidéos sans transcription analysées par Gemini")
    p.add_argument("--audio-fallback-max", type=int, default=4)
    p.add_argument("--min-confidence", type=float, default=0.6)
    p.add_argument("--strict", action="store_true", help="Exclure les propos rapportés/locuteur incertain")
    p.add_argument("--platforms", default="youtube,dailymotion,archive")
    p.add_argument("--no-articles", action="store_true")
    p.add_argument("--max-minutes", type=float, default=30, help="Durée max par recherche ; au-delà, résultats partiels")
    p.add_argument("--allow-heuristic", action="store_true", help="Continuer même si l'IA ne répond pas (déconseillé)")
    p.add_argument("--out", default="results", help="Dossier de sortie")
    p.add_argument("--batch-id", default="", help="Nom du dossier du lot (défaut : date + heure)")
    p.add_argument("--search-id", default="", help="Nom du dossier de la recherche (défaut : d'après la cible)")
    return p.parse_args(argv)


def _split(s: str) -> list[str]:
    return [x.strip() for x in (s or "").split(",") if x.strip()]


def options_for(a, target: str, claim: str, ttype: str) -> Options:
    speakers = "tous" if a.anyone else a.speakers
    return Options(target=target.strip(), claim=claim.strip(), target_type=ttype, speakers=speakers, also=_split(a.also),
                   langs=_split(a.langs) or ["fr"], max_videos=a.max_videos, max_articles=a.max_articles,
                   authority=a.authority, since=a.since,
                   video_fallback_max=a.video_fallback_max if a.video_fallback_max is not None else a.max_videos,
                   audio_fallback_max=a.audio_fallback_max, min_confidence=a.min_confidence, strict=a.strict,
                   platforms=_split(a.platforms), articles=not a.no_articles, max_minutes=a.max_minutes,
                   allow_heuristic=a.allow_heuristic)


def run_one(o: Options, llm, outdir: Path, search_id: str) -> int:
    try:
        plan, vres, ares = run(o, llm)
    except PlanError as e:
        logging.error("%s", e)
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / "ERREUR.txt").write_text(str(e), encoding="utf-8")
        print(f"ÉCHEC : {e}")
        return 3
    except Exception as e:  # noqa: BLE001 - une recherche en erreur ne doit pas faire perdre les autres
        logging.error("Recherche « %s » en erreur : %s", o.target, e)
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / "ERREUR.txt").write_text(f"{type(e).__name__}: {e}\n\n{traceback.format_exc()[-3000:]}", encoding="utf-8")
        return 4
    stats = {"videos_scanned": len(vres), "articles_scanned": len(ares)}
    p = report.build(o, plan, vres, ares, outdir, stats, search_id)
    print(f"Rapport : {p}")
    summ = os.environ.get("GITHUB_STEP_SUMMARY")
    if summ:
        with open(summ, "a", encoding="utf-8") as f:
            f.write(p.read_text(encoding="utf-8")[:300_000] + "\n\n")
    return 0


def main(argv=None) -> int:
    a = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    llm = LLMRouter.from_env()
    if not llm.has_text:
        logging.warning("Aucune clé Gemini/LLM : impossible de juger le contenu. Définis le secret GEMINI_API_KEYS.")
        return 2
    lot = a.batch_id or dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    if a.target:
        sid = a.search_id or "01-" + slugify(f"{a.target} {a.claim}", 50)
        searches = [{"target": a.target, "claim": a.claim, "type": a.target_type, "id": sid}]
    else:
        text = a.batch if a.batch is not None else Path(a.batch_file).read_text(encoding="utf-8")
        searches = batch.parse(text, a.target_type)
        if not searches:
            print("Aucune recherche lisible. Format : 'cible | sujet | type ; ...'")
            return 2
    batch_dir = Path(a.out) / lot
    codes = []
    for s in searches:
        logging.info("===== Recherche %s : %s | %s (%s) =====", s["id"], s["target"], s["claim"], s["type"])
        codes.append(run_one(options_for(a, s["target"], s["claim"], s["type"]), llm, batch_dir / s["id"], s["id"]))
    idx = batch.merge(batch_dir)
    batch.update_results_readme(Path(a.out))
    print(f"Index du lot : {idx}")
    if pipeline.TIMED_OUT.is_set():  # des tâches bloquées pourraient retenir le processus : on sort proprement
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)
    return 0 if any(c == 0 for c in codes) else (codes[0] if codes else 1)


if __name__ == "__main__":
    sys.exit(main())
