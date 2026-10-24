"""Télécharge uniquement les extraits trouvés (de « début » à « fin ») pour le montage : yt-dlp + ffmpeg."""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

from .util import fmt_ts, slugify

log = logging.getLogger("qf.clips")


def plan_clips(data: dict, pad_before: float = 2.0, pad_after: float = 1.0, min_conf: float = 0.0,
               only_own_voice: bool = False) -> list[dict]:
    """Liste des extraits à télécharger, avec marges. Pure (testable) : ne touche ni au réseau ni au disque."""
    out, n = [], 0
    for v in data.get("videos", []):
        for m in v.get("moments", []):
            if m.get("confidence", 0) < min_conf or m.get("start_seconds") is None or m.get("end_seconds") is None:
                continue
            if only_own_voice and m.get("who_says") not in ("target", "other_named"):
                continue
            n += 1
            s = max(0.0, m["start_seconds"] - pad_before)
            e = m["end_seconds"] + pad_after
            who = m.get("speaker_name") or m.get("who_says", "")
            name = f"{n:02d}_{slugify(who, 20)}_{slugify(v['title'], 35)}_{fmt_ts(m['start_seconds']).replace(':', 'm')}"
            out.append({"url": v["url"], "start": s, "end": e, "name": name, "quote": m.get("quote", "")})
    return out


def download(clip: dict, outdir: Path, height: int = 720) -> Path | None:
    base = outdir / clip["name"]
    cmd = [sys.executable, "-m", "yt_dlp", "--no-warnings", "--no-playlist",
           "--download-sections", f"*{clip['start']:.1f}-{clip['end']:.1f}", "--force-keyframes-at-cuts",
           "-f", f"bv*[height<={height}]+ba/b[height<={height}]/b", "--merge-output-format", "mp4",
           "-o", f"{base}.%(ext)s", clip["url"]]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    except (subprocess.SubprocessError, OSError) as e:
        log.warning("échec %s : %s", clip["name"], e)
        return None
    f = next(iter(outdir.glob(clip["name"] + ".*")), None)
    if f is None:
        log.warning("échec %s : %s", clip["name"], (r.stderr.strip().splitlines() or ["?"])[-1][:160])
    return f


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="quote_finder.clips", description="Télécharge les extraits (début→fin) d'un results.json")
    ap.add_argument("results_json")
    ap.add_argument("--out", default=None, help="dossier de sortie (défaut : clips/<recherche>)")
    ap.add_argument("--pad-before", type=float, default=2.0, help="marge avant (s)")
    ap.add_argument("--pad-after", type=float, default=1.0, help="marge après (s)")
    ap.add_argument("--min-confidence", type=float, default=0.8)
    ap.add_argument("--own-voice-only", action="store_true", help="exclure les propos rapportés / locuteur incertain")
    ap.add_argument("--height", type=int, default=720)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    rj = Path(a.results_json)
    plan = plan_clips(json.loads(rj.read_text(encoding="utf-8")), a.pad_before, a.pad_after, a.min_confidence, a.own_voice_only)
    out = Path(a.out) if a.out else Path("clips") / rj.parent.name
    out.mkdir(parents=True, exist_ok=True)
    done = []
    for i, c in enumerate(plan, 1):
        log.info("[%d/%d] %s  (%s → %s)", i, len(plan), c["name"], fmt_ts(c["start"]), fmt_ts(c["end"]))
        f = download(c, out, a.height)
        if f:
            done.append(f)
    if done:  # liste prête pour : ffmpeg -f concat -safe 0 -i montage_list.txt -c copy montage.mp4
        (out / "montage_list.txt").write_text("".join(f"file '{f.name}'\n" for f in sorted(done)), encoding="utf-8")
    print(f"{len(done)}/{len(plan)} extraits dans {out}")
    return 0 if done or not plan else 1


if __name__ == "__main__":
    sys.exit(main())
