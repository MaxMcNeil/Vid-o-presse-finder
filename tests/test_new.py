import json

from quote_finder import authority, batch, report, scan
from quote_finder.models import ArticleCandidate, Hit, Plan, Segment, VideoCandidate


def V(title, channel, dur=600, subs=None):
    return VideoCandidate("youtube", "x", "https://www.youtube.com/watch?v=x", title, "", channel, "2026-01-01", dur,
                          channel_subs=subs)


def test_authority_prefers_serious_sources_over_clickbait():
    plan = Plan("x", [["a"]], [], [], [], ["fr"], authoritative_sources=["RTS Info", "admin.ch", "Le Temps"])
    good = [V("La Suisse commence à larguer Microsoft", "RTS Info"), V("Mediapart publie ses preuves", "franceinfo"),
            V("Interview", "Le Temps", subs=20_000), V("Discours", "Reuters")]
    bad = [V("Microsoft JUST LOST Switzerland — Government PCs Are ABANDONING Windows", "Tech Insider 99", subs=3000),
           V("La Suisse abandonne Microsoft. Vous êtes suivants. #shorts", "Chaîne X", dur=40),
           V("Switzerland BANS Microsoft Completely", "random", subs=500)]
    for v in good:
        assert authority.score_video(v, plan)[0] >= 0.6, v.channel
    for v in bad:
        assert authority.score_video(v, plan)[0] < 0.4, v.title


def test_article_authority_from_domain_even_behind_google_news():
    plan = Plan("x", [["a"]], [], [], [], ["fr"], authoritative_sources=["heidi.news"])
    g = ArticleCandidate("Le Monde", "https://news.google.com/rss/articles/CBM", "t", domain="lemonde.fr")
    assert authority.score_article(g, plan)[0] >= 0.85
    assert authority.score_article(ArticleCandidate("x", "https://www.bk.admin.ch/a"), plan)[0] == 1.0
    assert authority.score_article(ArticleCandidate("x", "https://heidi.news/a"), plan)[0] >= 0.75
    assert authority.score_article(ArticleCandidate("x", "https://blog-inconnu.net/a"), plan)[0] < 0.6


def test_batch_parse_many_searches():
    s = batch.parse("La Suisse | abandonne Microsoft | pays ; Jordan Bardella | mensonges ;\n"
                    "Microsoft | espionnage | entité\nsouveraineté numérique ; # commentaire ; La Suisse | abandonne Microsoft")
    assert [x["target"] for x in s] == ["La Suisse", "Jordan Bardella", "Microsoft", "souveraineté numérique"]
    assert [x["type"] for x in s] == ["pays", "auto", "entite", "auto"]
    assert s[0]["id"].startswith("01-la-suisse") and s[3]["id"].startswith("04-")
    assert len(batch.parse(" ; ".join(f"cible{i}" for i in range(30)))) == batch.MAX_SEARCHES


def test_refine_times_snaps_to_transcript_lines():
    segs = [Segment(t, x) for t, x in [(100.0, "bonjour à tous"), (103.2, "nous avons décidé de quitter"),
                                         (106.9, "les logiciels de Microsoft pour"), (110.4, "des raisons de souveraineté"),
                                         (113.8, "numérique merci"), (117.0, "question suivante")]]
    h = Hit(98, "nous avons décidé de quitter les logiciels de Microsoft pour des raisons de souveraineté numérique",
            "literal", "target", "", 0.9, t_end=125)
    scan.refine_times([h], segs)
    assert h.t == 103.2 and h.t_end == 117.0 and h.timing == "sous-titres"
    far = Hit(400, "phrase qui n'existe pas du tout dans la vidéo", "literal", "target", "", 0.9, t_end=405)
    scan.refine_times([far], segs)
    assert far.t == 400 and far.timing == "ia"  # rien de sûr : on garde l'horodatage de l'IA


def test_montage_files_and_batch_merge(tmp_path):
    from quote_finder.pipeline import Options, VideoResult
    plan = Plan("résumé", [["a"]], ["suisse"], [], [], ["fr"], target_type="pays")
    v = V("Le Conseil fédéral et Microsoft", "RTS Info")
    v.authority, v.authority_why = 0.9, "média de référence"
    hits = [Hit(103.2, "nous quittons Microsoft", "literal", "target", "Conseiller fédéral", 0.9, t_end=117.0,
                speaker_role="official", timing="sous-titres"),
            Hit(300, "on en parle", "paraphrase", "unknown", "", 0.7, t_end=310)]
    o = Options("La Suisse", "abandonne Microsoft", target_type="pays", speakers="tous")
    for i in (1, 2):
        report.build(o, plan, [VideoResult(v, list(hits), "transcript")], [], tmp_path / "lot" / f"0{i}-x", search_id=f"0{i}-x")
    m = json.loads((tmp_path / "lot" / "01-x" / "montage.json").read_text())
    c = m["clips"][0]
    assert c["start"] == 103.2 and c["end"] == 117.0 and c["in"] == 102.2 and c["out"] == 117.8
    assert c["start_tc"] == "00:01:43.200" and c["timing"] == "sous-titres" and c["order"] == 1
    assert m["clips"][1]["in"] == 297.5  # horodatage IA : marge plus large
    (tmp_path / "lot" / "03-y").mkdir()
    (tmp_path / "lot" / "03-y" / "ERREUR.txt").write_text("IA indisponible")
    idx = batch.merge(tmp_path / "lot").read_text()
    assert "IA indisponible" in idx and "| 01 | La Suisse |" in idx
    allc = json.loads((tmp_path / "lot" / "montage.json").read_text())["clips"]
    assert len(allc) == 4 and [x["order"] for x in allc] == [1, 2, 3, 4]
    batch.update_results_readme(tmp_path)
    assert "lot/INDEX.md" in (tmp_path / "README.md").read_text()
    from quote_finder.clips import plan_clips
    pc = plan_clips(allc and {"clips": allc}, limit=3)
    assert len(pc) == 3 and pc[0]["start"] == 102.2 and pc[0]["end"] == 117.8


def test_llm_call_never_exceeds_its_time_limit():
    import time
    import pytest
    from quote_finder.keys import KeyPool
    from quote_finder.llm import GeminiClient, QuotaExhausted

    class Slow:
        def post(self, *a, **k):
            time.sleep(0.3)
            return type("R", (), {"status_code": 503, "text": "overloaded"})()

        def get(self, *a, **k):
            import requests
            raise requests.ConnectionError("pas de réseau")
    c = GeminiClient(KeyPool("g", ["k"], 0), ["gemini-x-flash"], Slow())
    t0 = time.time()
    with pytest.raises(QuotaExhausted):
        c.generate_json("x", until=time.time() + 1.5)
    assert time.time() - t0 < 6
