"""Expérience 2 : retrouver le vrai lien par le titre (Bing web RSS, DuckDuckGo) + Gemini url_context avec rotation des clés."""
import json, sys, time, re, html as H, urllib.parse as up
sys.path.insert(0, ".")
import requests
from quote_finder.keys import keys_from_env
UA = {"User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Mobile Safari/537.36",
      "Accept-Language": "fr,en;q=0.8"}
DOM = {"The Guardian": "theguardian.com", "Assemblée nationale": "assemblee-nationale.fr", "Radio France": "radiofrance.fr",
       "France 24": "france24.com", "Reuters": "reuters.com", "belgium.representation.ec.europa.eu": "ec.europa.eu",
       "The New York Times": "nytimes.com"}
src = json.load(open("scripts/exp_articles.json"))
cases = []
for c in src:
    t = re.sub(r"\s+-\s+[^-]{2,40}$", "", c["title"]).strip()
    cases.append({"title": t, "dom": DOM.get(c["source"], ""), "source": c["source"]})
out = []


def bing(q):
    r = requests.get("https://www.bing.com/search", params={"q": q, "format": "rss", "setlang": "fr"}, headers=UA, timeout=25)
    links = re.findall(r"<link>(https?://[^<]+)</link>", r.text)
    return [H.unescape(l) for l in links if "bing.com" not in l][:6], r.status_code


def ddg(q):
    r = requests.post("https://html.duckduckgo.com/html/", data={"q": q}, headers=UA, timeout=25)
    links = re.findall(r'class="result__a" href="([^"]+)"', r.text)
    res = []
    for l in links:
        l = H.unescape(l)
        if "uddg=" in l:
            l = up.unquote(up.parse_qs(up.urlparse(l).query).get("uddg", [l])[0])
        res.append(l)
    return res[:6], r.status_code


def readable(u):
    import trafilatura
    try:
        r = requests.get(u, headers=UA, timeout=20)
        return len(trafilatura.extract(r.text) or "") if r.status_code == 200 else -r.status_code
    except Exception as e:  # noqa
        return type(e).__name__


keys = keys_from_env("GEMINI")
for i, c in enumerate(cases):
    res = {"title": c["title"][:90], "source": c["source"]}
    for name, fn, q in (("bing_site", bing, f'"{c["title"]}" site:{c["dom"]}' if c["dom"] else f'"{c["title"]}"'),
                        ("bing_any", bing, f'"{c["title"]}"'), ("ddg_any", ddg, f'"{c["title"]}"')):
        try:
            links, code = fn(q)
            res[name] = {"code": code, "links": [(l[:90], readable(l)) for l in links[:4]]}
        except Exception as e:  # noqa
            res[name] = {"err": f"{type(e).__name__}: {str(e)[:120]}"}
        time.sleep(2)
    # Gemini url_context : clé suivante à chaque article, sur le 1er lien trouvé
    target = next((l for k in ("bing_site", "bing_any", "ddg_any") for l, _ in res.get(k, {}).get("links", [])), None)
    if target and keys:
        key = keys[i % len(keys)]
        body = {"contents": [{"parts": [{"text": f"Lis cette page et recopie mot à mot son premier paragraphe : {target}"}]}],
                "tools": [{"url_context": {}}]}
        for model in ("gemini-3.6-flash", "gemini-3.8-flash", "gemini-flash-latest"):
            try:
                r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                                  params={"key": key}, json=body, timeout=90)
                if r.status_code == 200:
                    j = r.json()["candidates"][0]
                    txt = "".join(p.get("text", "") for p in j["content"]["parts"])
                    res["gemini_url_context"] = {"model": model, "meta": json.dumps(j.get("urlContextMetadata") or j.get("url_context_metadata") or {})[:200], "text": txt[:200]}
                    break
                res["gemini_url_context"] = {"model": model, "err": f"HTTP {r.status_code} {r.text[:120]}"}
            except Exception as e:  # noqa
                res["gemini_url_context"] = {"model": model, "err": type(e).__name__}
    out.append(res)
    print(json.dumps(res, ensure_ascii=False)[:1200], flush=True)
import os
os.makedirs("state", exist_ok=True)
json.dump(out, open("state/experiment_articles.json", "w"), ensure_ascii=False, indent=1)
