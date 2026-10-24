from quote_finder import authority, registry
from quote_finder.models import ArticleCandidate, VideoCandidate
from quote_finder.sources import media_sites


def test_registry_puts_concerned_country_first():
    s = registry.for_search(["CH"], ["fr"], ["heidi.news", "Le Temps"], limit=20)
    doms = [x.domain for x in s]
    assert doms[0] == "admin.ch" and "rts.ch" in doms[:6] and "heidi.news" in doms and "reuters.com" in doms
    assert doms.index("rts.ch") < doms.index("reuters.com")
    # sans pays connu : on se sert des langues
    assert registry.for_search([], ["de"], limit=3)[0].domain in {d.domain for d in registry.SOURCES["DE"]}
    assert "RTS - Radio Télévision Suisse" in registry.youtube_channels(["CH"], ["fr"], 6)


def test_registry_feeds_authority_scores():
    assert authority.score_domain("rts.ch")[0] >= 0.85 and authority.score_domain("www.admin.ch")[0] == 1.0
    assert authority.score_domain("tagesschau.de")[0] >= 0.85
    v = VideoCandidate("web", "1", "https://www.rts.ch/info/suisse/123-microsoft.html", "t", "", "RTS", "2026-09-01", 300)
    assert authority.score_video(v)[0] >= 0.85


def test_site_search_keeps_only_that_site(monkeypatch):
    def bing(q, n, lg, http=None):
        return [ArticleCandidate("RTS", "https://www.rts.ch/info/a.html", "La Confédération lâche Microsoft", domain="rts.ch"),
                ArticleCandidate("x", "https://blog.example/b", "hors site", domain="blog.example")]
    monkeypatch.setattr(media_sites.news, "bing_news", bing)
    monkeypatch.setattr(media_sites.news, "google_news", lambda *a, **k: [])
    arts = media_sites.search_articles([registry.Source("rts.ch", "RTS", True)], "Microsoft Confédération", ["fr"])
    assert [a.url for a in arts] == ["https://www.rts.ch/info/a.html"]


def test_probe_turns_broadcaster_page_into_video(monkeypatch, tmp_path):
    pages = {"https://www.rts.ch/info/a.html": {"extractor_key": "SRGSSR", "id": "15000001", "title": "Le Conseil fédéral et Microsoft",
                                                  "duration": 154, "upload_date": "20260915", "webpage_url": "https://www.rts.ch/info/a.html",
                                                  "formats": [{}]},
             "https://www.lemonde.fr/b.html": {"extractor_key": "Youtube", "id": "abcdefghijk", "title": "Interview", "duration": 600,
                                                "formats": [{}]},
             "https://www.rts.ch/info/sans-video.html": None}
    monkeypatch.setattr(media_sites, "_ytdlp_json", lambda url, timeout=60: pages.get(url))
    arts = [ArticleCandidate("RTS", u, "t", domain=authority.domain_of(u)) for u in pages]
    vids = media_sites.videos_from_articles(arts, workers=1)
    by = {v.platform: v for v in vids}
    assert by["web"].url == "https://www.rts.ch/info/a.html" and by["web"].published == "2026-09-15" and by["web"].channel == "RTS"
    assert by["youtube"].url == "https://www.youtube.com/watch?v=abcdefghijk"  # vidéo YouTube intégrée dans l'article
    assert len(vids) == 2


def test_arabic_text_is_kept_and_matched():
    from quote_finder import scan
    from quote_finder.util import normalize, slugify
    assert normalize("السعودية تعلن رؤية ٢٠٣٠") == normalize("السعوديه تعلن رؤيه 2030")  # variantes d'écriture, chiffres
    assert scan.term_present(normalize("أعلنت المملكة العربية السعودية اليوم رؤية 2030"), normalize("السعودية"))
    s = slugify("محمد بن سلمان | رؤية 2030")
    assert s.isascii() and len(s) >= 6 and s == slugify("محمد بن سلمان | رؤية 2030")
    assert slugify("La Suisse abandonne Microsoft") == "la-suisse-abandonne-microsoft"


def test_gcc_countries_all_represented():
    doms = [s.domain for s in registry.for_search(["GCC"], ["ar"], limit=8)]
    assert {"spa.gov.sa", "wam.ae", "qna.org.qa", "kuna.net.kw", "bna.bh", "omannews.gov.om"} <= set(doms)
    assert authority.score_domain("www.spa.gov.sa")[0] == 1.0 and authority.score_domain("alarabiya.net")[0] >= 0.85


def test_languages_follow_countries_and_script():
    import argparse
    from quote_finder.cli import options_for, parse_args
    a = parse_args(["--target", "x"])
    assert options_for(a, "قطر", "الغاز الطبيعي", "pays").langs[0] == "ar"
    assert options_for(a, "Italia", "governo", "pays").langs == ["fr", "en"]  # l'italien est ajouté après le plan (pays IT)


def test_ai_answers_in_any_json_shape_are_accepted():
    """Cas réel du 24/10 : Gemini a renvoyé une liste au lieu de {"hits": [...]} -> toutes les vidéos plantaient."""
    from quote_finder import pipeline, scan
    from quote_finder.models import Plan
    from quote_finder.util import items_of
    one = {"t": 12, "quote": "Der Bund will weg von Microsoft", "verdict": "literal", "who_says": "other_named", "confidence": 0.9}
    for shape in ([one], {"hits": [one]}, [{"hits": [one]}], one, {"hits": one}):
        assert len(items_of(shape, "hits")) == 1, shape
    assert items_of(None, "hits") == [] and items_of({"hits": None}, "hits") == [] and items_of("texte", "hits") == []

    class L:
        def generate_json(self, prompt, **k):
            return [one]
    plan = Plan("x", [["microsoft"]], ["schweiz"], [], [], ["de"], target_type="pays")
    hits = scan.judge_video_direct(L(), plan, pipeline.Options("La Suisse", "x"), "https://www.youtube.com/watch?v=A-cipLy-c7s")
    assert [h.t for h in hits] == [12]
