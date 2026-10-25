"""Expérience : quelles méthodes gratuites lisent le texte des articles depuis GitHub ? -> state/experiment_articles.json"""
import json, sys, time, re
sys.path.insert(0, ".")
import requests
from quote_finder.keys import keys_from_env
UA = {"User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Mobile Safari/537.36",
      "Accept-Language": "fr,en;q=0.8"}
cases = json.load(open("scripts/exp_articles.json"))
out = []


def step(res, name, fn):
    t0 = time.time()
    try:
        v = fn()
        res[name] = {"ok": bool(v), "len": len(v or ""), "s": round(time.time() - t0, 1), "head": (v or "")[:160]}
    except Exception as e:  # noqa
        res[name] = {"ok": False, "err": f"{type(e).__name__}: {str(e)[:160]}", "s": round(time.time() - t0, 1)}


for c in cases:
    res = {"url": c["url"][:100], "source": c["source"]}
    real = {"u": c["url"]}

    def gdec():
        if "news.google.com" not in c["url"]:
            return c["url"]
        from googlenewsdecoder import gnewsdecoder
        d = gnewsdecoder(c["url"], interval=1)
        if d.get("status"):
            real["u"] = d["decoded_url"]
            return d["decoded_url"]
        raise RuntimeError(str(d.get("message"))[:150])
    step(res, "google_decode", gdec)

    def direct():
        import trafilatura
        r = requests.get(real["u"], headers=UA, timeout=25, allow_redirects=True)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        real["final"] = r.url
        return trafilatura.extract(r.text) or ""
    step(res, "direct_trafilatura", direct)

    def jina():
        r = requests.get("https://r.jina.ai/" + real["u"], headers={"Accept": "text/plain"}, timeout=40)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code} {r.text[:100]}")
        return r.text if len(r.text) > 400 else ""
    step(res, "jina_reader", jina)

    def wayback():
        u = real.get("final") or real["u"]
        r = requests.get("https://archive.org/wayback/available", params={"url": u}, timeout=25).json()
        snap = ((r.get("archived_snapshots") or {}).get("closest") or {}).get("url")
        if not snap:
            raise RuntimeError("pas d'archive")
        import trafilatura
        h = requests.get(snap, headers=UA, timeout=40).text
        return trafilatura.extract(h) or ""
    step(res, "wayback", wayback)

    def gem_url():
        key = keys_from_env("GEMINI")[0]
        body = {"contents": [{"parts": [{"text": f"Lis cet article et recopie mot à mot ses 3 premiers paragraphes : {real['u']}"}]}],
                "tools": [{"url_context": {}}]}
        r = requests.post("https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent",
                          params={"key": key}, json=body, timeout=120)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code} {r.text[:150]}")
        j = r.json()
        meta = (j["candidates"][0].get("url_context_metadata") or j["candidates"][0].get("urlContextMetadata") or {})
        txt = "".join(p.get("text", "") for p in j["candidates"][0]["content"]["parts"])
        return f"[{json.dumps(meta)[:200]}] " + txt
    step(res, "gemini_url_context", gem_url)
    out.append(res)
    print(json.dumps(res, ensure_ascii=False)[:900], flush=True)

import os
os.makedirs("state", exist_ok=True)
json.dump(out, open("state/experiment_articles.json", "w"), ensure_ascii=False, indent=1)
