import time
import pytest

from quote_finder import scan
from quote_finder.keys import KeyPool, parse_keys
from quote_finder.llm import GeminiClient, QuotaExhausted
from quote_finder.models import Hit, Plan, Segment
from quote_finder.transcripts import parse_vtt_srt
from quote_finder.util import fmt_ts, parse_ts, parse_iso_duration, parse_json_loose

PLAN = Plan("Saddam possède des ADM", [["saddam", "hussein"], ["weapons of mass destruction", "wmd", "armes de destruction massive"],
            ["has", "possess", "stockpile", "arsenal"]], ["bill clinton", "president clinton"], [], [], ["en"])


def test_timestamps():
    assert fmt_ts(75) == "01:15" and fmt_ts(3725) == "1:02:05"
    assert parse_ts("1:02:05") == 3725 and parse_ts("12:03") == 723 and parse_ts("90") == 90 and parse_ts("x") is None
    assert parse_iso_duration("PT1H2M3S") == 3723
    assert parse_json_loose('```json\n{"a": 1}\n```') == {"a": 1}


def test_keys_parse_and_rotation():
    assert parse_keys("a, b\nc;d") == ["a", "b", "c", "d"]
    pool = KeyPool("t", ["k1", "k2"], min_interval=0)
    got = {pool.acquire(), pool.acquire()}
    assert got == {"k1", "k2"}
    pool.exhaust("k1")
    assert pool.available() == 1 and pool.acquire() == "k2"
    pool.exhaust("k2")
    assert pool.acquire() is None


def test_throttle():
    pool = KeyPool("t", ["k"], min_interval=0.3)
    t0 = time.time(); pool.acquire(); pool.acquire()
    assert time.time() - t0 >= 0.28


class R:
    def __init__(self, code, body):
        self.status_code, self._b, self.text = code, body, str(body)
    def json(self): return self._b


class FakeHttp:
    def __init__(self, seq): self.seq, self.calls = seq, []
    def post(self, url, params=None, json=None, timeout=None):
        self.calls.append(params["key"]); return self.seq.pop(0)


def ok(text): return R(200, {"candidates": [{"content": {"parts": [{"text": text}]}}]})


def test_gemini_rotates_on_daily_quota():
    pool = KeyPool("g", ["k1", "k2"], 0)
    http = FakeHttp([R(429, "RESOURCE_EXHAUSTED GenerateRequestsPerDay quota"), ok('{"hits": []}')])
    assert GeminiClient(pool, "m", http).generate_json("x") == {"hits": []}
    assert http.calls[0] != http.calls[1] and pool.available() == 1


def test_gemini_all_exhausted():
    pool = KeyPool("g", ["k1"], 0)
    http = FakeHttp([R(429, "PerDay")])
    with pytest.raises(QuotaExhausted):
        GeminiClient(pool, "m", http).generate_json("x")


def test_vtt_dedup_auto_captions():
    raw = """WEBVTT

00:00:01.000 --> 00:00:03.000
saddam hussein has

00:00:03.000 --> 00:00:05.000
saddam hussein has
weapons of mass destruction
"""
    segs = parse_vtt_srt(raw)
    assert [s.text for s in segs] == ["saddam hussein has", "weapons of mass destruction"]
    assert segs[1].start == 3.0


def test_fuzzy_terms_handle_asr_errors():
    assert scan.term_present("sadam husein", "saddam")  # faute de transcription
    assert not scan.term_present("the cat sat on the mat", "saddam")
    assert scan.term_present("weapon of mass destruction program", "weapons of mass destruction")


def test_find_excerpts_and_repeats():
    segs = [Segment(i * 5.0, "bla bla") for i in range(120)]
    segs[10] = Segment(50.0, "Saddam Hussein has weapons of mass destruction")
    segs[100] = Segment(500.0, "I repeat: sadam has wmd and an arsenal")
    ex = scan.find_excerpts(segs, PLAN)
    assert len(ex) == 2
    assert ex[0].start < 60 < ex[0].end + 1 and "[00:50]" in ex[0].text
    assert ex[1].start > 400


def test_no_excerpt_when_only_one_group_matches():
    segs = [Segment(0, "saddam"), Segment(10, "weather is nice")]
    assert scan.find_excerpts(segs, PLAN) == []  # besoin de >= 2 groupes sur 3


def test_dedupe_and_keep():
    a = Hit(50, "q", "literal", "target", "", 0.9)
    b = Hit(53, "q2", "literal", "target", "", 0.7)
    assert len(scan.dedupe_hits([a, b])) == 1
    assert scan.keep_hit(a, PLAN, False, [], 0.5, True)
    rep = Hit(10, "q", "paraphrase", "reported", "", 0.9)
    assert scan.keep_hit(rep, PLAN, False, [], 0.5, False) and not scan.keep_hit(rep, PLAN, False, [], 0.5, True)
    assert not scan.keep_hit(Hit(1, "q", "mention", "target", "", 0.9), PLAN, False, [], 0.5, False)
    assert not scan.keep_hit(Hit(1, "q", "denial", "target", "", 0.9), PLAN, False, [], 0.5, False)
    other = Hit(1, "q", "literal", "other_named", "Colin Powell", 0.9)
    assert not scan.keep_hit(other, PLAN, False, [], 0.5, False)
    assert scan.keep_hit(other, PLAN, False, ["Colin Powell"], 0.5, False)
    assert scan.keep_hit(other, PLAN, True, [], 0.5, False)


def test_keys_from_env_uses_secret_names(monkeypatch):
    from quote_finder.keys import keys_from_env
    monkeypatch.setenv("GEMINI_API_KEYS", "a\nb\nc\nd\ne\n")
    monkeypatch.setenv("FALLBACK_LLM_KEYS", "x,y")
    monkeypatch.setenv("YOUTUBE_API_KEYS", "")
    assert keys_from_env("GEMINI") == ["a", "b", "c", "d", "e"]
    assert keys_from_env("FALLBACK_LLM") == ["x", "y"]
    assert keys_from_env("YOUTUBE") == []


def test_gemini_switches_model_on_404_and_discovers():
    class H(FakeHttp):
        def post(self, url, params=None, json=None, timeout=None):
            self.calls.append(url.split("models/")[1].split(":")[0])
            return self.seq.pop(0)
        def get(self, url, params=None, timeout=None):
            return R(200, {"models": [
                {"name": "models/gemini-3.8-flash", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/gemini-3.9-flash", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/gemini-3.9-flash-lite", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/embedding-1", "supportedGenerationMethods": ["embedContent"]}]})
    err = R(404, "This model models/gemini-3.8-flash is no longer available to new users")
    http = H([err, ok('{"hits": []}')])
    c = GeminiClient(KeyPool("g", ["k"], 0), ["gemini-3.8-flash", "gemini-flash-latest"], http)
    assert c.generate_json("x") == {"hits": []}
    assert http.calls[0] == "gemini-3.8-flash" and http.calls[1] == "gemini-3.9-flash"


def test_gemini_rotates_model_on_repeated_503(monkeypatch):
    import quote_finder.llm as L
    monkeypatch.setattr(L.time, "sleep", lambda s: None)

    class H(FakeHttp):
        def post(self, url, params=None, json=None, timeout=None):
            self.calls.append(url.split("models/")[1].split(":")[0]); return self.seq.pop(0)
        def get(self, url, params=None, timeout=None):
            return R(200, {"models": []})
    busy = R(503, "currently experiencing high demand")
    http = H([busy, busy, ok('{"hits": []}')])
    c = GeminiClient(KeyPool("g", ["k"], 0), ["m-a", "m-b"], http)
    assert c.generate_json("x") == {"hits": []}
    assert http.calls == ["m-a", "m-a", "m-b"]


def test_bing_news_extracts_real_url():
    from quote_finder.sources import news
    xml = b"""<rss xmlns:News="x"><channel><item><title>T</title>
      <link>http://www.bing.com/news/apiclick.aspx?ref=FexRss&amp;url=https%3a%2f%2fwww.lemonde.fr%2fa.html&amp;c=1</link>
      <pubDate>Mon, 05 Oct 2026 10:00:00 GMT</pubDate><News:Source>Le Monde</News:Source></item></channel></rss>"""
    class H:
        def get(self, url, **kw): return type("R", (), {"status_code": 200, "content": xml})()
    a = news.bing_news("q", http=H())[0]
    assert a.url == "https://www.lemonde.fr/a.html" and a.published == "2026-10-05" and a.source == "Le Monde"


def test_fuzzy_fallback_without_rapidfuzz(monkeypatch):
    monkeypatch.setattr(scan, "HAVE_RAPIDFUZZ", False)
    assert scan.term_present("sadam husein a des armes", "saddam")
    assert scan.term_present("weapon of mass destruction program", "weapons of mass destruction")
    assert not scan.term_present("the cat sat on the mat", "saddam")
    assert not scan.term_present("good morning france", "weapons of mass destruction")
