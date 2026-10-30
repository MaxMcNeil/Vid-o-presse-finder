"""Mode reportage, liens Google Actualités retrouvés par DuckDuckGo, articles pertinents sans mots-clés."""
from types import SimpleNamespace

from quote_finder import pipeline, scan
from quote_finder.models import ArticleCandidate, Hit


def _o(**k):
    base = dict(min_confidence=0.6, strict=False, reportage=True, speakers="tous", also=[])
    base.update(k)
    return SimpleNamespace(**base)


def _plan():
    return SimpleNamespace(target_type="sujet", target_aliases=[], term_groups=[["piratage"]], official_speakers=[])


def test_reportage_kept_and_ranked_after_declarations():
    rep = Hit(t=10, quote="Des pirates chinois ont infiltré...", verdict="reportage", who_says="unknown", confidence=0.8)
    lit = Hit(t=20, quote="Nous avons été piratés", verdict="literal", who_says="target", confidence=0.8)
    assert scan.keep_hit(rep, _plan(), _o())
    assert not scan.keep_hit(rep, _plan(), _o(strict=True))
    assert not scan.keep_hit(rep, _plan(), _o(reportage=False))
    assert scan.hit_score(lit) > scan.hit_score(rep)
    assert not scan.keep_hit(Hit(t=1, quote="x", verdict="mention", who_says="unknown", confidence=0.9), _plan(), _o())


def test_prompts_ask_for_reportage():
    assert '"reportage"' in scan.JUDGE_PROMPT or "reportage" in scan.JUDGE_PROMPT
    assert "reportage" in scan.VIDEO_PROMPT


class _Resp:
    def __init__(self, text, code=200):
        self.text, self.status_code = text, code


class _HTTP:
    def __init__(self, text, code=200):
        self.r = _Resp(text, code)

    def post(self, *a, **k):
        return self.r


def test_ddg_finds_same_media_link_only():
    html = ('<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.autre.com%2Fx">x</a>'
            '<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.lemonde.fr%2Fart%2F123.html&rut=1">y</a>')
    assert pipeline._ddg_find("Titre", "lemonde.fr", _HTTP(html)) == "https://www.lemonde.fr/art/123.html"
    assert pipeline._ddg_find("Titre", "lemonde.fr", _HTTP("", 403)) == ""
    assert pipeline._ddg_find("", "lemonde.fr", _HTTP(html)) == ""


def test_relevant_article_without_keywords_gets_its_lead_judged(monkeypatch):
    from quote_finder import article_text
    text = "Des hackers liés à Pékin ont compromis des serveurs.\nLes autorités américaines enquêtent.\nSuite."
    monkeypatch.setattr(article_text, "read", lambda *a, **k: (text, "direct"))
    seen = {}

    def judge(llm, plan, o, meta, ex, kind):
        seen["ex"] = ex
        return [Hit(t=None, quote="Des hackers liés à Pékin", verdict="reportage", who_says="unknown", confidence=0.8)]
    monkeypatch.setattr(scan, "judge_excerpts", judge)
    a = ArticleCandidate("Le Monde", "https://lemonde.fr/a", "Cyberattaque géante", "2026-10-29")
    a.relevance = 8
    plan = SimpleNamespace(target_type="sujet", target_aliases=[], term_groups=[["piratage"], ["chine"]],
                           claim_summary="s", official_speakers=[])
    o = SimpleNamespace(target="Chine", claim="piratage mondial")
    r = pipeline.analyze_article(a, plan, o, None)
    assert r.hits and "hackers" in seen["ex"][0].text
