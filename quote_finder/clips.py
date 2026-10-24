"""Télécharge uniquement les extraits (in → out) listés dans montage.json, puis assemble un premier montage.

    python -m quote_finder.clips results/<lot>/montage.json --max 20 --min-score 0.6

Sortie : clips/<lot>/NN_....mp4, montage_list.txt (ffmpeg concat) et montage.mp4 (si ffmpeg est présent).
Sur GitHub : option « telecharger_extraits » du workflow (fichiers fournis en artefact, jamais dans le dépôt).
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path

from .util import fmt_ts, slugify

log = logging.getLogger("qf.clips")


def plan_clips(data: dict, pad_before: float | None = None, pad_after: float | None = None, min_conf: float = 0.0,
               only_own_voice: bool = False, min_score: float = 0.0, limit: int | None = None) -> list[dict]:
    """Liste des extraits à télécharger. Accepte un montage.json (clé « clips ») ou un results.json (clé « videos »).
    Pure (testable) : ne touche ni au réseau ni au disque."""
    src = []
    if "clips" in data:
        for c in data["clips"]:
            src.append(c)
    else:  # ancien format results.json
        for v in data.get("videos", []):
            for m in v.get("moments", []):
                if m.get("start_seconds") is None or m.get("end_seconds") is None:
                    continue
                src.append({"source_url": v["url"], "title": v.get("title", ""), "start": m["start_seconds"],
                            "end": m["end_seconds"], "in": m["start_seconds"] - 2.0, "out": m["end_seconds"] + 1.0,
                            "quote": m.get("quote", ""), "speaker": m.get("speaker_name", ""),
                            "who_says": m.get("who_says", ""), "confidence": m.get("confidence", 0),
                            "score": m.get("confidence", 0)})
    out = []
    for c in src:
        if c.get("confidence", 0) < min_conf or c.get("score", 0) < min_score:
            continue
        if only_own_voice and c.get("who_says") not in ("target", "other_named"):
            continue
        s = max(0.0, c["start"] - pad_before) if pad_before is not None else max(0.0, c.get("in", c["start"]))
        e = c["end"] + pad_after if pad_after is not None else c.get("out", c["end"])
        n = len(out) + 1
        who = c.get("speaker") or c.get("who_says", "")
        name = f"{n:02d}_{slugify(who, 20)}_{slugify(c.get('title', ''), 35)}_{fmt_ts(c['start']).replace(':', 'm')}"
        out.append({"url": c["source_url"], "start": s, "end": e, "name": name, "quote": c.get("quote", "")})
        if limit and len(out) >= limit:
            break
    return out


def download(clip: dict, outdir: Path, height: int = 720) -> Path | None:
    base = outdir / clip["name"]
    cmd = [sys.executable, "-m", "yt_dlp", "--no-warnings", "--no-playlist",
           "--download-sections", f"*{clip['start']:.2f}-{clip['end']:.2f}", "--force-keyframes-at-cuts",
           "-f", f"bv*[height<={height}]+ba/b[height<={height}]/b", "--merge-output-format", "mp4",
           "-o", f"{base}.%(ext)s", clip["url"]]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except (subprocess.SubprocessError, OSError) as e:
        log.warning("échec %s : %s", clip["name"], e)
        return None
    f = next(iter(outdir.glob(clip["name"] + ".*")), None)
    if f is None:
        log.warning("échec %s : %s", clip["name"], (r.stderr.strip().splitlines() or ["?"])[-1][:160])
    return f


def assemble(files: list[Path], outdir: Path) -> Path | None:
    """Ré-encode chaque extrait au même format puis concatène : montage.mp4 (premier jet, dans l'ordre de solidité)."""
    if not files or not shutil.which("ffmpeg"):
        return None
    norm = []
    for f in files:
        g = f.with_name(f.stem + "_n.mp4")
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(f), "-vf",
               "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,fps=30",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-ar", "48000", "-ac", "2", str(g)]
        if subprocess.run(cmd, capture_output=True).returncode == 0:
            norm.append(g)
    if not norm:
        return None
    lst = outdir / "montage_norm.txt"
    lst.write_text("".join(f"file '{g.name}'\n" for g in norm), encoding="utf-8")
    out = outdir / "montage.mp4"
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)],
                       capture_output=True)
    for g in norm:
        g.unlink(missing_ok=True)
    return out if r.returncode == 0 else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="quote_finder.clips", description="Télécharge les extraits d'un montage.json")
    ap.add_argument("json_file", help="montage.json (ou results.json)")
    ap.add_argument("--out", default=None, help="dossier de sortie (défaut : clips/<lot>)")
    ap.add_argument("--pad-before", type=float, default=None, help="marge avant (s) ; défaut : celle de montage.json")
    ap.add_argument("--pad-after", type=float, default=None, help="marge après (s)")
    ap.add_argument("--min-confidence", type=float, default=0.0)
    ap.add_argument("--min-score", type=float, default=0.0)
    ap.add_argument("--max", type=int, default=25, help="nombre max d'extraits")
    ap.add_argument("--own-voice-only", action="store_true", help="exclure les propos rapportés / locuteur incertain")
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--no-assemble", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    rj = Path(a.json_file)
    plan = plan_clips(json.loads(rj.read_text(encoding="utf-8")), a.pad_before, a.pad_after, a.min_confidence,
                      a.own_voice_only, a.min_score, a.max)
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
        if not a.no_assemble:
            m = assemble(sorted(done), out)
            if m:
                print(f"Premier montage : {m}")
    print(f"{len(done)}/{len(plan)} extraits dans {out}")
    return 0 if done or not plan else 1


if __name__ == "__main__":
    sys.exit(main())
