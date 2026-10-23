from quote_finder.models import Plan
import json

from quote_finder import pipeline, report, transcripts
from quote_finder.models import ArticleCandidate, Segment, Transcript, VideoCandidate
from quote_finder.sources import news, other_video, youtube


class FakeLLM:
    has_text = True
    has_video = True

    def __init__(self):
        self.prompts = []

    def generate_json(self, prompt, video_url=None, inline_media=None, media_key=""):
        self.prompts.append((prompt[:40], video_url))
        if "Tu prépares une recherche" in prompt:
            return {"claim_summary": "Saddam aurait des ADM",
                    "term_groups": [["saddam"], ["weapons of mass destruction", "wmd"]],
                    "person_aliases": ["Bill Clinton"], "youtube_queries": ["clinton saddam wmd"], "news_queries": ["clinton wmd"]}
        if inline_media:
            return {"hits": [{"t": 36, "quote": "il ment", "verdict": "paraphrase", "who_says": "other_named", "speaker_name": "X", "confidence": 0.9}]}
        if video_url:
            return {"hits": [{"t": "03:10", "quote": "he has wmd", "verdict": "literal", "who_says": "target", "confidence": 0.9}]}
        if "article de presse" in prompt:
            return {"hits": [{"quote": "Clinton said Saddam has WMD", "verdict": "paraphrase", "who_says": "reported", "confidence": 0.8}]}
        if "CLINTON-SPEECH" in prompt:
            return {"hits": [{"t": 50, "quote": "Saddam has weapons of mass destruction", "verdict": "literal", "who_says": "target", "speaker_name": "Bill Clinton", "confidence": 0.95},
                             {"t": 500, "quote": "I say again, wmd", "verdict": "paraphrase", "who_says": "target", "confidence": 0.8}]}
        return {"hits": []}


def test_end_to_end(monkeypatch, tmp_path):
    vids = [VideoCandidate("youtube", "AAA", "https://www.youtube.com/watch?v=AAA", "CLINTON-SPEECH 1998", "", "WH", "1998-02-17T00:00:00Z", 3000),
            VideoCandidate("youtube", "BBB", "https://www.youtube.com/watch?v=BBB", "Clinton on WMD (clickbait)", "", "Chan", "2020-01-01T00:00:00Z", 600),
            VideoCandidate("dailymotion", "DDD", "https://www.dailymotion.com/video/DDD", "DM no subs", "", "BFM", "2026-10-09", 300),
            VideoCandidate("youtube", "CCC", "https://www.youtube.com/watch?v=CCC", "No captions", "", "Chan", "2021-01-01T00:00:00Z", 600)]
    monkeypatch.setattr(youtube, "search", lambda *a, **k: list(vids))
    monkeypatch.setattr(other_video, "dailymotion", lambda *a, **k: [])
    monkeypatch.setattr(other_video, "archive_org", lambda *a, **k: [])
    monkeypatch.setattr(news, "gdelt", lambda *a, **k: [ArticleCandidate("reuters.com", "https://reuters.com/x", "Clinton says...", "2003-01-01")])
    monkeypatch.setattr(news, "google_news", lambda *a, **k: [])
    monkeypatch.setattr(news, "bing_news", lambda *a, **k: [])
    monkeypatch.setattr(news, "guardian", lambda *a, **k: [ArticleCandidate("The Guardian", "https://g.co/y", "Full text piece", "1998-02-18", text="Intro.\nClinton said Saddam has WMD today.\nMore.")])
    monkeypatch.setattr(news, "nyt", lambda *a, **k: [])

    segs_a = [Segment(i * 5.0, "talk") for i in range(120)]
    segs_a[10] = Segment(50.0, "CLINTON-SPEECH Saddam has weapons of mass destruction")
    segs_a[100] = Segment(500.0, "I say again, wmd, saddam")
    segs_b = [Segment(i * 5.0, "unrelated cooking show") for i in range(50)]

    def fake_tr(v, langs):
        return {"AAA": Transcript(segs_a, "en", "auto"), "BBB": Transcript(segs_b, "en", "manual")}.get(v.id)
    monkeypatch.setattr(transcripts, "get_transcript", fake_tr)

    monkeypatch.setattr(pipeline.media, "fetch_audio", lambda url, **k: b"audio")
    llm = FakeLLM()
    o = pipeline.Options("Bill Clinton", "Saddam has weapons of mass destruction", workers=1)
    plan, vres, ares = pipeline.run(o, llm)
    by = {r.video.id: r for r in vres}
    assert [h.t for h in by["AAA"].hits] == [50, 500]            # deux occurrences dans la même vidéo
    assert by["BBB"].hits == [] and "aucun passage" in by["BBB"].note  # titre trompeur : contenu sans rapport, aucun appel IA
    assert by["CCC"].method == "gemini-video" and by["CCC"].hits[0].t == 190  # repli analyse vidéo directe
    # jugements texte : 1 vidéo (AAA) + 1 article ; BBB (hors sujet) et le reuters injoignable n'en consomment aucun
    assert sum(1 for p, _ in llm.prompts if "Tu vérifies" in p) == 2
    assert by["DDD"].method == "gemini-audio" and by["DDD"].hits == []  # locuteur X non voulu (anyone=False) : écarté
    assert len(ares) == 2 and any(r.hits for r in ares)

    out = tmp_path / "r"
    p = report.build(o.person, o.claim, plan, vres, ares, out, {"videos_scanned": 3, "articles_scanned": 2})
    md = p.read_text()
    assert "start=50&end=" in md and "start=500&end=" in md and "00:50 → " in md and "1998-02-17" in md and "https://www.youtube.com/watch?v=AAA" in md
    data = json.loads((out / "results.json").read_text())
    assert data["videos"][0]["title"].startswith("CLINTON") and data["videos"][0]["moments"][0]["timestamp"] == "00:50"
    assert (out / "videos.csv").exists() and (out / "articles.csv").exists()
    m0 = data["videos"][0]["moments"][0]
    assert m0["start_seconds"] == 50 and m0["end_seconds"] > 50 and m0["duration_seconds"] >= 4  # fin estimée si absente
    rows = (out / "moments.csv").read_text(encoding="utf-8-sig").splitlines()
    assert rows[0].startswith("titre,") and "debut,fin,debut_s,fin_s,duree_s" in rows[0] and len(rows) >= 3


def test_source_failures_never_kill_the_run(monkeypatch, tmp_path):
    """Régression : le NYT renvoyait {"response": {"docs": null}} et faisait planter tout le run."""
    from quote_finder.sources import news
    from quote_finder import pipeline as P

    class H:
        def get(self, url, **kw):
            return type("R", (), {"status_code": 200, "json": lambda self: {"response": {"docs": None, "results": None}},
                                  "content": b"<rss/>"})()
    assert news.nyt(P.KeyPool("n", ["k"], 0), "q", 10, H()) == []
    assert news.guardian(P.KeyPool("g", ["k"], 0), "q", 10, H()) == []

    def boom(*a, **k): raise KeyError("x")
    monkeypatch.setattr(news, "gdelt", boom)
    assert P._safe(news.gdelt, "q") == []


def test_anyone_mode_is_announced_to_the_judge():
    from quote_finder import scan
    seen = {}

    class L:
        def generate_json(self, prompt, **k):
            seen["p"] = prompt
            return {"hits": []}
    plan = Plan("x", [["a"]], ["bardella"], [], [], ["fr"])
    ex = [scan.Excerpt(0, 10, 1, "[00:00] a")]
    scan.judge_excerpts(L(), plan, "Bardella", [], "il ment", {}, ex, anyone=True)
    assert "TOUT LOCUTEUR EST ACCEPTÉ" in seen["p"]
    scan.judge_excerpts(L(), plan, "Bardella", [], "il ment", {}, ex, anyone=False)
    assert "TOUT LOCUTEUR EST ACCEPTÉ" not in seen["p"]
