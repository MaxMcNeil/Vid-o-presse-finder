from __future__ import annotations

import csv
import json
from pathlib import Path

from .scan import rank_key
from .util import fmt_ts

VERDICT = {"literal": "à la lettre", "paraphrase": "dans le sens"}
WHO = {"target": "la personne parle elle-même", "other_named": "autre locuteur identifié",
       "reported": "propos rapportés par un tiers", "unknown": "locuteur non identifié avec certitude"}


def ts_url(url: str, platform: str, t: float | None) -> str:
    if t is None:
        return url
    s = int(t)
    if platform == "youtube":
        return f"{url}&t={s}s"
    if platform == "dailymotion":
        return f"{url}?start={s}"
    if platform == "archive":
        return f"{url}?start={s}"
    return url


def _best(h_list):
    return min((rank_key(h) for h in h_list), default=(9, 9, 0))


def build(person, claim, plan, vres, ares, outdir: Path, stats: dict | None = None) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    vm = sorted([r for r in vres if r.hits], key=lambda r: _best(r.hits))
    am = sorted([r for r in ares if r.hits], key=lambda r: _best(r.hits))

    # JSON
    data = {
        "person": person, "claim": claim, "plan": plan.to_dict(), "stats": stats or {},
        "videos": [{"title": r.video.title, "uploaded": r.video.published[:10], "platform": r.video.platform,
                    "channel": r.video.channel, "url": r.video.url, "method": r.method,
                    "moments": [{"timestamp": fmt_ts(h.t), "seconds": h.t, "link": ts_url(r.video.url, r.video.platform, h.t),
                                 **h.to_dict()} for h in r.hits]} for r in vm],
        "articles": [{"title": r.article.title, "date": r.article.published, "source": r.article.source,
                      "url": r.article.url, "verified_on_full_text": r.verified,
                      "passages": [h.to_dict() for h in r.hits]} for r in am],
        "unreadable_articles": [{"title": r.article.title, "date": r.article.published, "source": r.article.source,
                                 "url": r.article.url} for r in ares if not r.hits and not r.verified],
        "unverifiable_videos": [{"title": r.video.title, "url": r.video.url, "reason": r.note}
                                for r in vres if r.method == "none"],
    }
    (outdir / "results.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    with open(outdir / "videos.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["titre", "date_upload", "timestamps", "url", "plateforme", "locuteur", "type_de_match", "confiance"])
        for r in vm:
            w.writerow([r.video.title, r.video.published[:10], " ; ".join(fmt_ts(h.t) for h in r.hits), r.video.url,
                        r.video.platform, " ; ".join(h.speaker_name or h.who_says for h in r.hits),
                        " ; ".join(h.verdict for h in r.hits), " ; ".join(f"{h.confidence:.2f}" for h in r.hits)])
    with open(outdir / "articles.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["titre", "date", "source", "url", "texte_complet_verifie"])
        for r in am:
            w.writerow([r.article.title, r.article.published, r.article.source, r.article.url, r.verified])

    L = [f"# {person} — « {claim} »", "",
         f"_{plan.claim_summary}_", "",
         f"**{len(vm)} vidéo(s)** et **{len(am)} article(s)** retenus"
         + (f" (sur {stats['videos_scanned']} vidéos et {stats['articles_scanned']} articles analysés)" if stats else "") + ".", ""]
    L += ["## Vidéos", ""]
    if not vm:
        L.append("Aucune vidéo confirmée.\n")
    for r in vm:
        v = r.video
        L.append(f"### {v.title or v.id}")
        L.append(f"- **Upload** : {v.published[:10] or 'inconnu'} · **{v.platform}** · {v.channel}")
        L.append(f"- **URL** : {v.url}")
        L.append(f"- **Analyse** : " + {"transcript": "transcription", "gemini-audio": "audio écouté par IA (Gemini)"}.get(
            r.method, "vidéo analysée par IA (Gemini)"))
        L.append("- **Moments** :")
        for h in r.hits:
            L.append(f"  - [{fmt_ts(h.t)}]({ts_url(v.url, v.platform, h.t)}) — {VERDICT.get(h.verdict, h.verdict)}, "
                     f"{WHO.get(h.who_says, h.who_says)}{' (' + h.speaker_name + ')' if h.speaker_name else ''}, "
                     f"confiance {h.confidence:.0%}")
            L.append(f"    > {h.quote}")
        L.append("")
    L += ["## Articles de presse", ""]
    if not am:
        L.append("Aucun article confirmé.\n")
    for r in am:
        a = r.article
        L.append(f"- **{a.title}** — {a.source}, {a.published or 'date inconnue'} — {a.url}")
        if not r.verified:
            L.append("  - ⚠️ vérifié sur le chapô/résumé seulement")
        for h in r.hits[:2]:
            L.append(f"  > {h.quote}")
    L.append("")
    unv = [r for r in ares if not r.hits and not r.verified]
    if unv:
        L += [f"## Articles trouvés mais non lisibles ({len(unv)}) — à vérifier à la main", ""]
        L += [f"- {r.article.title} — {r.article.source}, {r.article.published or '?'} — {r.article.url}" for r in unv[:15]]
        L.append("")
    un = [r for r in vres if r.method == "none"]
    if un:
        L += ["## Vidéos non vérifiables (pas de transcription, quota IA épuisé ou erreur)", ""]
        L += [f"- {r.video.title or r.video.id} — {r.video.url} — {r.note}" for r in un[:40]]
        L.append("")
    p = outdir / "report.md"
    p.write_text("\n".join(L), encoding="utf-8")
    return p
