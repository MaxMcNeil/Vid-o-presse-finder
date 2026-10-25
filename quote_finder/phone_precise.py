"""Précision par le téléphone (Termux) : recale les extraits sur les sous-titres YouTube, puis renvoie sur GitHub.

Pourquoi le téléphone : depuis GitHub, YouTube bloque la lecture des sous-titres ; depuis une connexion de particulier,
non. Aucun compte YouTube : sous-titres publics, lus comme par un visiteur non connecté.

Principe « bonus, jamais bloquant » :
  - GitHub fait toute la recherche sans attendre le téléphone (précision Gemini ±1 à ±5 s) ;
  - quand le téléphone passe (commande `qf precis`, ou tâche planifiée), il améliore les lots récents à ±1 s ;
  - chaque extrait garde une trace de la tentative (tel_status) : rien n'est retraité inutilement,
    un blocage temporaire est retenté au passage suivant ;
  - téléphone éteint, sans réseau ou en économie d'énergie : rien ne casse, ce sera fait plus tard.

    python -m quote_finder.phone_precise [--days 14] [--max-videos 40] [--no-push] [--dry-run]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import random
import subprocess
import sys
import time
from pathlib import Path

from . import batch, report, scan
from .models import Hit, Segment, VideoCandidate
from .util import fmt_ts

log = logging.getLogger("qf.tel")
HIT_FIELDS = set(Hit.__dataclass_fields__)
DONE = ("sous-titres",)                      # déjà précis
FINAL = ("ok", "pas de sous-titres", "pas retrouvé dans les sous-titres")  # ne pas retenter...
ALGO = 2  # ...sauf si la méthode de recalage a été améliorée depuis la tentative (version enregistrée avec)


def pending(m: dict) -> bool:
    """Extrait encore à recaler par le téléphone ?"""
    if m.get("timing") in DONE:
        return False
    if m.get("tel_status") == "ok" or m.get("tel_status") == "pas de sous-titres":
        return False
    return not (m.get("tel_status") in FINAL and m.get("tel_algo", 1) >= ALGO)


class Blocked(Exception):
    """YouTube refuse temporairement (trop de requêtes) : on s'arrête et on retentera plus tard."""


def _api_tracks(video_id: str, langs: list[str]) -> list[list[Segment]]:
    """youtube-transcript-api : rapide, mais parfois refusé par YouTube (IpBlocked)."""
    import html
    import re
    from youtube_transcript_api import YouTubeTranscriptApi
    try:
        tl = YouTubeTranscriptApi().list(video_id)
    except Exception as e:  # noqa: BLE001
        name = type(e).__name__
        if name in ("RequestBlocked", "IpBlocked", "TooManyRequests", "YouTubeRequestFailed", "PoTokenRequired"):
            raise Blocked(name) from e
        return []  # sous-titres désactivés, vidéo privée ou supprimée...
    cands = list(tl)
    cands.sort(key=lambda c: (0 if c.language_code.split("-")[0] in langs else 1, c.is_generated))
    out = []
    for c in cands[:3]:
        try:
            f = c.fetch()
        except Exception as e:  # noqa: BLE001
            if type(e).__name__ in ("RequestBlocked", "IpBlocked", "TooManyRequests", "PoTokenRequired"):
                raise Blocked(type(e).__name__) from e
            continue
        segs = [Segment(float(s.start), re.sub(r"\s+", " ", html.unescape(s.text)).strip()) for s in f]
        segs = [s for s in segs if s.text]
        if segs:
            out.append(segs)
    return out


def _ytdlp_tracks(video_id: str, langs: list[str]) -> list[list[Segment]]:
    """yt-dlp : mis à jour très souvent pour suivre YouTube ; sous-titres d'origine (« -orig ») et langues voulues."""
    import glob
    import os
    import tempfile
    from .transcripts import parse_vtt_srt
    want = ",".join([".*-orig"] + [f"{l}" for l in langs] + [f"{l}-.*" for l in langs])
    with tempfile.TemporaryDirectory() as d:
        try:
            p = subprocess.run([sys.executable, "-m", "yt_dlp", "--skip-download", "--write-subs", "--write-auto-subs",
                                "--sub-langs", want, "--sub-format", "vtt", "--no-warnings", "--sleep-subtitles", "1",
                                "-o", os.path.join(d, "s.%(ext)s"), f"https://www.youtube.com/watch?v={video_id}"],
                               capture_output=True, text=True, timeout=180)
        except (subprocess.SubprocessError, OSError):
            return []
        err = (p.stderr or "") + (p.stdout or "")
        files = sorted(glob.glob(os.path.join(d, "*.vtt")), key=lambda f: (0 if "-orig" in f else 1, f))
        if not files:
            if any(x in err for x in ("not a bot", "429", "Too Many Requests", "Sign in to confirm")):
                raise Blocked("yt-dlp: " + ("vérification anti-robot" if "bot" in err else "trop de requêtes"))
            return []
        out = []
        for f in files[:3]:
            segs = parse_vtt_srt(open(f, encoding="utf-8", errors="replace").read())
            if segs:
                out.append(segs)
        return out


def fetch_original(video_id: str, langs: list[str]) -> list[list[Segment]]:
    """Sous-titres dans la langue ORIGINALE de la vidéo (les citations sont dans cette langue), puis les langues voulues.
    Jamais de traduction. Deux moyens : youtube-transcript-api, puis yt-dlp si le premier est refusé ou vide."""
    blocked = None
    try:
        tracks = _api_tracks(video_id, langs)
        if tracks:
            return tracks
    except Blocked as e:
        blocked = e
    except ImportError:
        pass
    try:
        return _ytdlp_tracks(video_id, langs)
    except Blocked as e:
        raise Blocked(f"{blocked} puis {e}" if blocked else str(e)) from e


def _rebuild(search_dir: Path, data: dict) -> int:
    """Réécrit results.json, montage.json/csv et ajoute une section au rapport à partir des moments mis à jour."""
    from .pipeline import Options, VideoResult
    o = Options(data.get("target", ""), data.get("claim", ""), target_type=data.get("target_type", "auto"),
                speakers=data.get("speakers", "auto"), authority=data.get("authority_level", "forte"))
    vm = []
    for v in data.get("videos", []):
        vc = VideoCandidate(v.get("platform", "youtube"), v.get("video_id") or report.video_id(v["url"], v.get("platform", "")),
                            v["url"], v.get("title", ""), "", v.get("channel", ""), v.get("uploaded", ""), v.get("duration"),
                            authority=v.get("authority", 0.0), authority_why=v.get("authority_why", ""))
        hits = [Hit(**{k: m[k] for k in HIT_FIELDS if k in m}) for m in v.get("moments", [])]
        vm.append(VideoResult(vc, hits, v.get("method", "")))
        for m, h in zip(v.get("moments", []), hits):  # champs dérivés cohérents
            m.update(start=fmt_ts(h.t), end=fmt_ts(h.t_end), start_seconds=h.t, end_seconds=h.t_end,
                     start_tc=report.tc(h.t), end_tc=report.tc(h.t_end), timestamp=fmt_ts(h.t),
                     duration_seconds=report.dur(h), link=report.ts_url(v["url"], vc.platform, h.t),
                     clip_link=report.clip_link(v["url"], vc.platform, h))
    (search_dir / "results.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    clips = report.montage_clips(o, vm, search_dir.name)
    report.write_montage(clips, search_dir, {"target": o.target, "target_type": o.target_type, "claim": o.claim,
                                             "speakers": o.speakers, "authority_level": o.authority})
    return len(clips)


def _report_note(search_dir: Path, changed: list[tuple[str, dict]]) -> None:
    p = search_dir / "report.md"
    if not p.exists() or not changed:
        return
    L = p.read_text(encoding="utf-8").split("\n## Précision (téléphone)")[0].rstrip()
    L += "\n\n## Précision (téléphone)\n\n" + \
        f"Recalé sur les sous-titres YouTube le {dt.date.today().isoformat()} (±1-2 s) — ces horodatages remplacent ceux ci-dessus :\n\n"
    for title, m in changed:
        L += f"- [{m['start']} → {m['end']}]({m['clip_link']}) — {title[:70]}\n  > {m['quote'][:200]}\n"
    p.write_text(L + "\n", encoding="utf-8")


def process(results_dir: Path, days: int = 14, max_videos: int = 40, dry_run: bool = False,
            pause=(2.0, 5.0), fetch=fetch_original) -> dict:
    stats = {"videos": 0, "recalés": 0, "pas_de_sous_titres": 0, "non_retrouvés": 0, "lots": set(), "bloqué": False}
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    lots = sorted((p for p in results_dir.iterdir() if p.is_dir() and p.name[:10] >= since), reverse=True)
    today = dt.date.today().isoformat()
    for lot in lots:
        for sd in sorted(p for p in lot.iterdir() if p.is_dir() and (p / "results.json").exists()):
            data = json.loads((sd / "results.json").read_text(encoding="utf-8"))
            langs = (data.get("plan") or {}).get("languages") or ["fr", "en"]
            changed, touched = [], False
            for v in data.get("videos", []):
                if v.get("platform") != "youtube":
                    continue
                todo = [m for m in v.get("moments", []) if pending(m)]
                if not todo:
                    continue
                if stats["videos"] >= max_videos:
                    break
                vid = v.get("video_id") or report.video_id(v["url"], "youtube")
                stats["videos"] += 1
                try:
                    tracks = fetch(vid, langs)
                except Blocked as e:
                    log.warning("YouTube limite temporairement (%s) : on s'arrête, la suite au prochain passage", e)
                    stats["bloqué"] = True
                    break
                if not tracks:
                    for m in todo:
                        m["tel_status"], m["tel_date"] = "pas de sous-titres", today
                    stats["pas_de_sous_titres"] += len(todo)
                    touched = True
                    continue
                for m in todo:
                    h = Hit(**{k: m[k] for k in HIT_FIELDS if k in m})
                    if h.t is None:
                        continue
                    for segs in tracks:
                        trial = Hit(**{k: getattr(h, k) for k in HIT_FIELDS})
                        trial.timing = "ia"
                        scan.refine_times([trial], segs, radius=150.0 if h.timing == "non retrouvé" else 90.0)
                        if trial.timing == "sous-titres":
                            scan.finish_times([trial], v.get("duration"))
                            m.update(t=trial.t, t_end=trial.t_end, timing="sous-titres")
                            break
                    m["tel_date"], m["tel_algo"] = today, ALGO
                    if m.get("timing") == "sous-titres":
                        m["tel_status"] = "ok"
                        stats["recalés"] += 1
                        changed.append((v.get("title", ""), m))
                    else:
                        m["tel_status"] = "pas retrouvé dans les sous-titres"
                        stats["non_retrouvés"] += 1
                    touched = True
                time.sleep(random.uniform(*pause))  # rythme humain : pas de rafale vers YouTube
            if touched and not dry_run:
                _rebuild(sd, data)
                _report_note(sd, changed)
                stats["lots"].add(lot)
            if stats["bloqué"] or stats["videos"] >= max_videos:
                break
        if stats["bloqué"] or stats["videos"] >= max_videos:
            break
    if not dry_run:
        for lot in stats["lots"]:
            batch.merge(lot)
    stats["lots"] = sorted(p.name for p in stats["lots"])
    return stats


def _git(repo: Path, *args, check=True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=check)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="qf precis", description="Recale les extraits sur les sous-titres YouTube (téléphone)")
    ap.add_argument("--repo", default=".", help="dossier du dépôt (défaut : dossier courant)")
    ap.add_argument("--days", type=int, default=14, help="lots des N derniers jours")
    ap.add_argument("--max-videos", type=int, default=40, help="vidéos traitées au maximum par passage")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    repo = Path(a.repo).resolve()
    if not a.no_push:
        r = _git(repo, "pull", "--rebase", "-q", check=False)
        if r.returncode != 0:
            print("⚠ git pull impossible (réseau ?) :", (r.stderr or r.stdout).strip()[-200:])
            return 1
    st = process(repo / "results", a.days, a.max_videos, a.dry_run)
    print(f"Vidéos consultées : {st['videos']} · extraits recalés : {st['recalés']} · sans sous-titres : "
          f"{st['pas_de_sous_titres']} · non retrouvés : {st['non_retrouvés']}" + (" · ⚠ YouTube a limité, suite au prochain passage" if st["bloqué"] else ""))
    if a.dry_run or a.no_push:
        return 0
    if st["lots"]:
        _git(repo, "add", "results")
        if _git(repo, "diff", "--cached", "--quiet", check=False).returncode != 0:
            _git(repo, "-c", "user.name=quote-finder-tel", "-c", "user.email=tel@users.noreply.github.com", "commit", "-q", "-m",
                 f"précision téléphone : {st['recalés']} extrait(s) recalé(s) sur les sous-titres ({', '.join(st['lots'])})")
    ahead = _git(repo, "rev-list", "--count", "@{u}..HEAD", check=False).stdout.strip()
    if not ahead or ahead == "0":  # rien à envoyer (y compris un envoi raté lors d'un passage précédent)
        return 0
    for i in range(4):
        if _git(repo, "pull", "--rebase", "-q", check=False).returncode == 0 and _git(repo, "push", "-q", check=False).returncode == 0:
            print("✔ envoyé sur GitHub")
            return 0
        time.sleep(5 * (i + 1))
    print("⚠ envoi sur GitHub impossible pour l'instant : réessayé au prochain passage (le commit est gardé)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
