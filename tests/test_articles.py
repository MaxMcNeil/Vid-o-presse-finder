from quote_finder import article_text as at
from quote_finder.models import ArticleCandidate

LONG = "Paragraphe utile sur les engrais et le détroit d'Ormuz. " * 20


def A(url, title="Hormuz shipping disruption is starting to hit harvests"):
    return ArticleCandidate("Reuters", url, title, domain="reuters.com")


def test_cascade_direct_then_archive(monkeypatch):
    monkeypatch.setattr(at, "direct", lambda u: "")              # site fermé aux robots
    monkeypatch.setattr(at, "wayback", lambda u: LONG)           # mais archivé
    t, via = at.read(A("https://www.nytimes.com/x"), None, "s", None)
    assert via == "archive.org" and t == LONG


class L:
    def __init__(self, text, meta):
        self.text, self.meta, self.n = text, meta, 0

    def read_page(self, prompt):
        self.n += 1
        return {"text": self.text, "meta": self.meta}


def test_gemini_reads_google_link_and_reveals_real_url(monkeypatch):
    monkeypatch.setattr("quote_finder.pipeline.resolve_google_news", lambda a, http=None: "")
    ok = {"urlMetadata": [{"retrievedUrl": "https://www.reuters.com/world/hormuz-harvests/", "urlRetrievalStatus": "URL_RETRIEVAL_STATUS_SUCCESS"}]}
    a = A("https://news.google.com/rss/articles/CBMabc")
    budget = [1]
    t, via = at.read(a, L("Voici le texte :\n" + LONG, ok), "engrais", budget)
    assert via == "gemini" and a.url == "https://www.reuters.com/world/hormuz-harvests/" and budget == [0]
    assert not t.lower().startswith("voici")
    # budget épuisé : pas d'appel Gemini, raison claire
    t2, why = at.read(A("https://news.google.com/rss/articles/CBMdef"), L(LONG, ok), "x", budget)
    assert t2 == "" and "Google" in why


def test_gemini_failure_is_not_taken_as_text(monkeypatch):
    monkeypatch.setattr(at, "direct", lambda u: "")
    monkeypatch.setattr(at, "wayback", lambda u: "")
    bad = {"urlMetadata": [{"retrievedUrl": "https://x", "urlRetrievalStatus": "URL_RETRIEVAL_STATUS_ERROR"}]}
    t, why = at.read(A("https://www.reuters.com/a"), L("Je ne peux pas accéder à cette page " * 30, bad), "x", [3])
    assert t == "" and "robots" in why
    t, why = at.read(A("https://www.reuters.com/b"), L("INACCESSIBLE", {}), "x", [3])
    assert t == ""


def test_status_keeps_workflows_block(tmp_path, monkeypatch):
    from quote_finder import status
    monkeypatch.chdir(tmp_path)
    (tmp_path / "results").mkdir()
    out = tmp_path / "ETAT.md"
    out.write_text("# ancien\n\n<!-- workflows:start -->\n## Workflows\n- ❌ Find quotes : échec\n<!-- workflows:end -->\n")
    status.build(tmp_path / "results", out)
    t = out.read_text()
    assert "## Clés et quotas" in t and "- ❌ Find quotes : échec" in t and t.count("workflows:start") == 1
