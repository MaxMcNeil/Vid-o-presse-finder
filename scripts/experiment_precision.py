"""Expérience : quelles méthodes donnent des timestamps précis depuis GitHub ? Résultat dans state/experiment.json."""
import json, os, subprocess, sys, tempfile, time, glob, traceback
sys.path.insert(0, ".")
CASES = [  # (id YouTube, début approx., fin approx., langue, citation attendue)
    ("A-cipLy-c7s", 9, 50, "de", "Die Software Microsoft 365 wurde erst vor wenigen Monaten an Zehntausenden Arbeitsplätzen des Bundes installiert"),
    ("HUn5SJMJ2fg", 506, 536, "en", "Switzerland wants to become more digitally independent"),
    ("BffWWGOgcWs", 0, 26, "en", "Switzerland's federal administration, I'm quoting here, aims to reduce its dependency on Microsoft"),
]
out = {"date": time.strftime("%Y-%m-%d %H:%M"), "cases": []}


def step(name, fn, res):
    t0 = time.time()
    try:
        res[name] = {"ok": True, "out": fn(), "s": round(time.time() - t0, 1)}
    except Exception as e:  # noqa
        res[name] = {"ok": False, "err": f"{type(e).__name__}: {str(e)[:300]}", "s": round(time.time() - t0, 1)}


for vid, a, b, lang, quote in CASES:
    res = {"id": vid}
    url = f"https://www.youtube.com/watch?v={vid}"

    def tapi():
        from youtube_transcript_api import YouTubeTranscriptApi
        f = YouTubeTranscriptApi().fetch(vid, languages=[lang, "en", "fr", "de"])
        segs = [(s.start, s.text) for s in f]
        return {"n": len(segs), "near": [x for x in segs if a - 5 <= x[0] <= a + 15][:5]}
    step("transcript_api", tapi, res)

    def ytsubs():
        with tempfile.TemporaryDirectory() as d:
            p = subprocess.run([sys.executable, "-m", "yt_dlp", "--skip-download", "--write-auto-subs", "--write-subs", "--sub-langs",
                                f"{lang}.*,en.*", "--sub-format", "vtt", "-o", f"{d}/s.%(ext)s", url], capture_output=True, text=True, timeout=120)
            fs = glob.glob(f"{d}/*.vtt")
            if not fs:
                raise RuntimeError((p.stderr or p.stdout).strip().splitlines()[-1][:250] if (p.stderr or p.stdout) else "aucun fichier")
            return {"files": [os.path.basename(x) for x in fs]}
    step("ytdlp_subs", ytsubs, res)

    audio = {}

    def dl():
        d = tempfile.mkdtemp()
        p = subprocess.run([sys.executable, "-m", "yt_dlp", "-f", "bestaudio/best", "--download-sections", f"*{max(0,a-20)}-{b+20}",
                            "-x", "--audio-format", "wav", "--postprocessor-args", "ffmpeg:-ac 1 -ar 16000",
                            "-o", f"{d}/a.%(ext)s", url], capture_output=True, text=True, timeout=180)
        fs = glob.glob(f"{d}/a.*")
        if not fs:
            raise RuntimeError((p.stderr or p.stdout).strip().splitlines()[-1][:250] if (p.stderr or p.stdout) else "aucun fichier")
        audio["f"] = fs[0]
        return {"bytes": os.path.getsize(fs[0])}
    step("ytdlp_audio_section", dl, res)

    if audio.get("f"):
        def whisper():
            from faster_whisper import WhisperModel
            m = WhisperModel("small", device="cpu", compute_type="int8")
            segs, info = m.transcribe(audio["f"], language=lang, word_timestamps=True, vad_filter=True)
            words = [(round(w.start + max(0, a - 20), 2), w.word) for s in segs for w in s.words]
            return {"lang": info.language, "n_words": len(words), "first": words[:25]}
        step("faster_whisper_small", whisper, res)

    def gem():
        import requests
        from quote_finder.keys import keys_from_env
        key = keys_from_env("GEMINI")[0]
        body = {"contents": [{"parts": [
            {"file_data": {"file_uri": url}, "video_metadata": {"start_offset": f"{max(0,a-20)}s", "end_offset": f"{b+20}s", "fps": 2}},
            {"text": f"Transcris mot à mot ce passage avec, pour chaque phrase, l'horodatage de début et de fin en secondes depuis le DÉBUT DE LA VIDÉO "
                     f"(pas du segment). Puis donne le début et la fin exacts de la phrase : « {quote} ». "
                     'JSON : {"sentences":[{"start":s,"end":s,"text":"..."}],"quote_start":s,"quote_end":s}'}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0}}
        r = requests.post("https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent",
                          params={"key": key}, json=body, timeout=180)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code} {r.text[:250]}")
        return json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"])
    step("gemini_clip_offsets", gem, res)
    out["cases"].append(res)
    print(json.dumps(res, ensure_ascii=False)[:1500], flush=True)

os.makedirs("state", exist_ok=True)
json.dump(out, open("state/experiment.json", "w"), ensure_ascii=False, indent=1)
