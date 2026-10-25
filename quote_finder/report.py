"""Rapport d'une recherche : report.md, results.json, videos.csv, articles.csv, moments.csv et montage.json."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from .authority import stars
from .scan import hit_score
from .util import fmt_ts

VERDICT = {"literal": "à la lettre", "paraphrase": "dans le sens"}
WHO = {"target": "la cible (ou son représentant) parle", "other_named": "autre locuteur identifié",
       "reported": "propos rapportés par un tiers", "unknown": "locuteur non identifié avec certitude"}
ROLE = {"official": "officiel", "expert": "expert", "politician": "responsable politique", "journalist": "journaliste",
        "other": "autre"}
TYPE = {"personne": "personne", "pays": "pays", "entite": "entité", "sujet": "sujet"}
PAD = {"sous-titres": (1.0, 0.8), "ia-affiné": (1.2, 1.0), "ia": (2.5, 2.0)}
TIMING = {"sous-titres": "calé sur les sous-titres (±1-2 s)", "ia-affiné": "affiné par réécoute ciblée (±1 s)",
          "ia": "estimé par l'IA (±5 s)", "non retrouvé": "⚠️ non retrouvé à la réécoute : exclu du montage"}  # marges avant/après pour le montage, selon la précision


def ts_url(url: str, platform: str, t: float | None) -> str:
    if t is None:
        return url
    s = int(t)
    if platform == "youtube":
        return f"{url}&t={s}s"
    if platform in ("dailymotion", "archive"):
        return f"{url}?start={s}"
    return url


def tc(seconds: float | None) -> str:
    """Timecode HH:MM:SS.mmm (format attendu par ffmpeg et la plupart des logiciels de montage)."""
    if seconds is None:
        return ""
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def span(h) -> str:
    return f"{fmt_ts(h.t)} → {fmt_ts(h.t_end)}" if h.t_end is not None else fmt_ts(h.t)


def dur(h) -> float | None:
    return round(h.t_end - h.t, 1) if (h.t is not None and h.t_end is not None) else None


def video_id(url: str, platform: str) -> str:
    if platform == "youtube" and "v=" in url:
        return url.split("v=")[1].split("&")[0]
    return url.rstrip("/").split("/")[-1]


def clip_link(url: str, platform: str, h) -> str:
    """Lien qui ne lit QUE l'extrait (YouTube)."""
    if platform == "youtube" and h.t is not None and h.t_end is not None and "v=" in url:
        return f"https://www.youtube.com/embed/{video_id(url, platform)}?start={int(h.t)}&end={int(h.t_end) + 1}&autoplay=1"
    return ts_url(url, platform, h.t)


def _best(r) -> float:
    return max((hit_score(h, r.video.authority) for h in r.hits), default=0.0)


def montage_clips(o, vm, search_id: str = "") -> list[dict]:
    """Une entrée par extrait, triée du plus solide au moins solide : prête pour un montage automatique."""
    clips = []
    for r in vm:
        v = r.video
        for h in r.hits:
            if h.t is None or h.t_end is None or h.timing == "non retrouvé":
                continue
            pb, pa = PAD.get(h.timing, PAD["ia"])
            tin, tout = max(0.0, h.t - pb), h.t_end + pa
            if v.duration:
                tout = min(tout, float(v.duration))
            clips.append({
                "search": search_id, "target": o.target, "target_type": o.target_type, "claim": o.claim,
                "platform": v.platform, "video_id": video_id(v.url, v.platform), "source_url": v.url,
                "title": v.title, "channel": v.channel, "uploaded": (v.published or "")[:10],
                "source_authority": v.authority, "source_authority_why": v.authority_why,
                "start": round(h.t, 2), "end": round(h.t_end, 2), "duration": dur(h),
                "start_tc": tc(h.t), "end_tc": tc(h.t_end),
                "in": round(tin, 2), "out": round(tout, 2), "in_tc": tc(tin), "out_tc": tc(tout),
                "timing": h.timing, "quote": h.quote, "speaker": h.speaker_name, "speaker_role": h.speaker_role,
                "who_says": h.who_says, "verdict": h.verdict, "confidence": h.confidence,
                "score": hit_score(h, v.authority), "link": clip_link(v.url, v.platform, h),
            })
    clips.sort(key=lambda c: -c["score"])
    for i, c in enumerate(clips, 1):
        c["order"] = i
    return clips


MONTAGE_COLS = ["order", "search", "score", "title", "channel", "uploaded", "platform", "video_id", "source_url",
                "start_tc", "end_tc", "in_tc", "out_tc", "start", "end", "in", "out", "duration", "timing", "speaker",
                "speaker_role", "who_says", "verdict", "confidence", "source_authority", "quote", "link"]


def write_montage(clips: list[dict], outdir: Path, meta: dict) -> None:
    (outdir / "montage.json").write_text(json.dumps({**meta, "clips": clips}, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(outdir / "montage.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=MONTAGE_COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(clips)


def build(o, plan, vres, ares, outdir: Path, stats: dict | None = None, search_id: str = "") -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    vm = sorted([r for r in vres if r.hits], key=lambda r: -_best(r))
    am = sorted([r for r in ares if r.hits], key=lambda r: -r.article.authority)
    clips = montage_clips(o, vm, search_id)
    head = {"target": o.target, "target_type": o.target_type, "claim": o.claim, "speakers": o.speakers,
            "authority_level": o.authority}

    data = {
        **head, "plan": plan.to_dict(), "stats": stats or {},
        "videos": [{"title": r.video.title, "uploaded": r.video.published[:10], "platform": r.video.platform,
                    "video_id": video_id(r.video.url, r.video.platform), "duration": r.video.duration,
                    "channel": r.video.channel, "url": r.video.url, "method": r.method,
                    "authority": r.video.authority, "authority_why": r.video.authority_why,
                    "moments": [{"start": fmt_ts(h.t), "end": fmt_ts(h.t_end), "start_seconds": h.t, "end_seconds": h.t_end,
                                 "start_tc": tc(h.t), "end_tc": tc(h.t_end),
                                 "duration_seconds": dur(h), "timestamp": fmt_ts(h.t), "link": ts_url(r.video.url, r.video.platform, h.t),
                                 "clip_link": clip_link(r.video.url, r.video.platform, h), **h.to_dict()} for h in r.hits]} for r in vm],
        "articles": [{"title": r.article.title, "date": r.article.published, "source": r.article.source,
                      "domain": r.article.domain, "url": r.article.url, "verified_on_full_text": r.verified,
                      "read_via": getattr(r, "via", ""),
                      "authority": r.article.authority, "authority_why": r.article.authority_why,
                      "passages": [h.to_dict() for h in r.hits]} for r in am],
        "unreadable_articles": [{"title": r.article.title, "date": r.article.published, "source": r.article.source,
                                 "url": r.article.url, "authority": r.article.authority, "reason": r.note}
                                for r in ares if not r.hits and not r.verified],
        "unverifiable_videos": [{"title": r.video.title, "url": r.video.url, "reason": r.note}
                                for r in vres if r.method == "none"],
    }
    (outdir / "results.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    write_montage(clips, outdir, head)

    with open(outdir / "videos.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["titre", "date_upload", "chaine", "autorite", "extraits", "url", "plateforme", "locuteurs"])
        for r in vm:
            w.writerow([r.video.title, r.video.published[:10], r.video.channel, r.video.authority,
                        " ; ".join(span(h) for h in r.hits), r.video.url, r.video.platform,
                        " ; ".join(h.speaker_name or h.who_says for h in r.hits)])
    with open(outdir / "moments.csv", "w", newline="", encoding="utf-8-sig") as f:  # 1 ligne = 1 extrait
        w = csv.writer(f)
        w.writerow(["titre", "date_upload", "url", "plateforme", "debut", "fin", "debut_s", "fin_s", "duree_s", "precision",
                    "locuteur", "role", "qui_parle", "type_de_match", "confiance", "autorite_source", "citation", "lien_extrait"])
        for r in vm:
            for h in r.hits:
                w.writerow([r.video.title, r.video.published[:10], r.video.url, r.video.platform, fmt_ts(h.t), fmt_ts(h.t_end),
                            h.t if h.t is not None else "", h.t_end if h.t_end is not None else "", dur(h) or "", h.timing,
                            h.speaker_name, ROLE.get(h.speaker_role, h.speaker_role), h.who_says, h.verdict,
                            f"{h.confidence:.2f}", r.video.authority, h.quote, clip_link(r.video.url, r.video.platform, h)])
    with open(outdir / "articles.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["titre", "date", "source", "domaine", "autorite", "url", "texte_complet_verifie"])
        for r in am:
            a = r.article
            w.writerow([a.title, a.published, a.source, a.domain, a.authority, a.url, r.verified])

    title = f"{o.target} — « {o.claim} »" if o.claim else o.target
    L = [f"# {title}", "",
         f"**Cible** : {o.target} ({TYPE.get(o.target_type, o.target_type)}) · **Locuteurs** : "
         + ("la cible / ses représentants" if o.speakers == "cible" else "toute voix qui fait autorité")
         + f" · **Autorité exigée** : {o.authority}", "",
         f"_{plan.claim_summary}_", "",
         f"**{len(vm)} vidéo(s)**, **{len(clips)} extrait(s) pour le montage** et **{len(am)} article(s)** retenus"
         + (f" (sur {stats['videos_scanned']} vidéos et {stats['articles_scanned']} articles analysés)" if stats else "") + ".", ""]
    L += ["## Vidéos", ""]
    if not vm:
        L.append("Aucune vidéo confirmée.\n")
    for r in vm:
        v = r.video
        L.append(f"### {v.title or v.id}")
        L.append(f"- **Source** : {v.channel or '?'} · {stars(v.authority)} {v.authority_why} · **{v.platform}** · "
                 f"upload {v.published[:10] or 'inconnu'}" + (f" · pertinence estimée {v.relevance:.0f}/10" if v.relevance else ""))
        L.append(f"- **URL** : {v.url}")
        L.append("- **Analyse** : " + {"transcript": "transcription (horodatage calé sur les sous-titres)",
                                        "gemini-audio": "audio écouté par IA (horodatage ±5 s)"}.get(
            r.method, "vidéo analysée par IA (horodatage ±5 s)"))
        L.append("- **Extraits** :")
        for h in r.hits:
            d = dur(h)
            role = ROLE.get(h.speaker_role, "")
            L.append(f"  - [{span(h)}]({clip_link(v.url, v.platform, h)})" + (f" ({d:.0f} s)" if d else "")
                     + f" · {TIMING.get(h.timing, h.timing)}"
                     + f" — {VERDICT.get(h.verdict, h.verdict)}, {WHO.get(h.who_says, h.who_says)}"
                     + (f" ({h.speaker_name}{', ' + role if role else ''})" if h.speaker_name else (f" ({role})" if role else ""))
                     + f", confiance {h.confidence:.0%}")
            L.append(f"    > {h.quote}")
        L.append("")
    L += ["## Articles de presse", ""]
    if not am:
        L.append("Aucun article confirmé.\n")
    for r in am:
        a = r.article
        VIA = {"direct": "", "api": "", "archive.org": " · lu via les archives d'Internet", "gemini": " · lu par Gemini (site fermé aux robots)",
               "chapô": ""}
        L.append(f"- **{a.title}** — {a.source} {stars(a.authority)}, {a.published or 'date inconnue'} — {a.url}"
                 + VIA.get(getattr(r, "via", ""), ""))
        if not r.verified:
            L.append("  - ⚠️ vérifié sur le chapô/résumé seulement")
        for h in r.hits[:2]:
            L.append(f"  > {h.quote}" + (f" — {h.speaker_name}" if h.speaker_name else ""))
    L.append("")
    if clips:
        L += ["## Montage", "",
              "`montage.json` / `montage.csv` : un extrait par ligne, du plus solide au moins solide, avec `in`/`out` "
              "(marges incluses) et `start`/`end` (déclaration exacte), en secondes et en timecode `HH:MM:SS.mmm`.", ""]
    ana = [r for r in vres if not r.hits and r.method != "none"]
    if ana:
        L += [f"## Vidéos analysées sans passage correspondant ({len(ana)})", ""]
        L += [f"- {r.video.title or r.video.id} — {r.video.channel} — {r.video.url}" for r in ana[:30]]
        L.append("")
    unv = [r for r in ares if not r.hits and not r.verified and r.relevant_title]
    if unv:
        L += [f"## Articles de référence non lisibles ({len(unv)}) — à vérifier à la main", ""]
        L += [f"- {r.article.title} — {r.article.source} {stars(r.article.authority)}, {r.article.published or '?'} — {r.article.url}"
              for r in unv[:10]]
        L.append("")
    un = [r for r in vres if r.method == "none"]
    if un:
        L += ["## Vidéos non vérifiables (pas de transcription, quota IA épuisé ou erreur)", ""]
        L += [f"- {r.video.title or r.video.id} — {r.video.url} — {r.note}" for r in un[:30]]
        L.append("")
    d = plan.diag or {}
    meth: dict[str, int] = {}
    reasons: dict[str, int] = {}
    for r in vres:
        meth[r.method] = meth.get(r.method, 0) + 1
        if r.method == "none":
            reasons[r.note[:70]] = reasons.get(r.note[:70], 0) + 1
    L += ["## Diagnostic", "",
          f"- Plan de recherche : {'IA' if plan.source == 'llm' else '⚠️ plan de secours (IA indisponible), résultats peu fiables'}",
          f"- Vidéos candidates : {d.get('videos_candidates', '?')}, écartées pour autorité insuffisante : "
          f"{d.get('videos_low_authority', '?')} ; retenues par plateforme : {d.get('videos_by_platform', {})}",
          f"- Articles candidats : {d.get('articles_candidates', '?')}, écartés pour autorité insuffisante : "
          f"{d.get('articles_low_authority', '?')}",
          f"- Analyses : {meth}",
          f"- Horodatage des passages : {d.get('timing', {})}",
          f"- Lecture des articles (méthode → nombre) : {d.get('articles_read_via', {})}",
          f"- Sources de référence interrogées : {', '.join(d.get('reference_sites', [])) or '—'}",
          f"- Articles trouvés directement chez elles : {d.get('site_articles', '?')} {d.get('site_articles_by_site', '')}",
          f"- Pages ouvertes pour y chercher une vidéo : {d.get('site_pages_probed', '?')} → vidéos trouvées : {d.get('site_videos', '?')}",
          f"- Recherche dans les chaînes YouTube officielles : {d.get('official_channels', '?')}",
          f"- Vidéos des sources de référence (pertinence) : {'; '.join(d.get('site_videos_scored', [])) or 'aucune'}"]
    if reasons:
        L.append("- Vidéos non vérifiées : " + " ; ".join(f"{n}× {k}" for k, n in sorted(reasons.items(), key=lambda x: -x[1])[:4]))
    if not (d.get("videos_by_platform") or {}).get("youtube"):
        L.append(f"- ⚠️ Aucune vidéo YouTube retenue (clés YouTube chargées : {d.get('youtube_keys', 0)}). "
                 "Essaie l'autorité « moyenne » ou lance le workflow « Diagnostic ».")
    if d.get("youtube_keys_status"):
        L.append(f"- Clés YouTube en fin de recherche : {d['youtube_keys_status']}")
    for api, u in (d.get("llm_usage") or {}).items():
        used = u.get("modèles utilisés") or {}
        L.append(f"- IA {api} : clés {u.get('clés', '?')}" + (" ; modèles : " + ", ".join(f"{m} ×{n}" for m, n in used.items()) if used else "")
                 + (f" ; {u['appels']} appel(s)" if u.get("appels") else ""))
    L.append("- État détaillé des clés : `state/QUOTAS.md` à la racine du dépôt")
    L.append(f"- Durée : {d.get('elapsed_min', '?')} min" + (" — ⚠️ limite de temps atteinte, résultats partiels" if d.get("timed_out") else ""))
    L.append("")
    p = outdir / "report.md"
    p.write_text("\n".join(L), encoding="utf-8")
    return p
