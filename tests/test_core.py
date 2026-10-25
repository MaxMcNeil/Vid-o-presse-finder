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
    assert http.calls[0] != http.calls[1]


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


def _o(**k):
    from quote_finder.pipeline import Options
    return Options(**{"target": "Bill Clinton", "claim": "x", "speakers": "cible", "min_confidence": 0.5, **k})


def test_dedupe_and_keep():
    a = Hit(50, "q", "literal", "target", "", 0.9)
    b = Hit(53, "q2", "literal", "target", "", 0.7)
    assert len(scan.dedupe_hits([a, b])) == 1
    assert scan.keep_hit(a, PLAN, _o(strict=True))
    rep = Hit(10, "q", "paraphrase", "reported", "", 0.9)
    assert scan.keep_hit(rep, PLAN, _o()) and not scan.keep_hit(rep, PLAN, _o(strict=True))
    assert not scan.keep_hit(Hit(1, "q", "mention", "target", "", 0.9), PLAN, _o())
    assert not scan.keep_hit(Hit(1, "q", "denial", "target", "", 0.9), PLAN, _o())
    other = Hit(1, "q", "literal", "other_named", "Colin Powell", 0.9)
    assert not scan.keep_hit(other, PLAN, _o())
    assert scan.keep_hit(other, PLAN, _o(also=["Colin Powell"]))
    assert scan.keep_hit(other, PLAN, _o(speakers="tous"))


def test_official_speaker_counts_as_target_for_a_country():
    from quote_finder.models import Plan
    plan = Plan("x", [["a"]], ["suisse", "la suisse"], [], [], ["fr"], target_type="pays",
                official_speakers=["Karin Keller-Sutter", "Conseil fédéral"])
    h = Hit(1, "q", "literal", "other_named", "Karin Keller-Sutter", 0.9, speaker_role="official")
    assert scan.keep_hit(h, plan, _o(target="La Suisse", target_type="pays"))
    assert not scan.keep_hit(Hit(1, "q", "literal", "other_named", "Un youtubeur", 0.9), plan,
                             _o(target="La Suisse", target_type="pays"))


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
    err = R(404, "This model models/gemini-3.9-flash is no longer available to new users")
    http = H([err, ok('{"hits": []}')])
    c = GeminiClient(KeyPool("g", ["k"], 0), ["gemini-flash-latest"], http)
    assert c.generate_json("x") == {"hits": []}
    # aucun modèle imposé : le meilleur modèle découvert pour la clé est essayé d'abord, puis le suivant
    assert http.calls[0] == "gemini-3.9-flash" and http.calls[1] == "gemini-3.8-flash"


def test_gemini_each_key_uses_its_own_models():
    class H(FakeHttp):
        def post(self, url, params=None, json=None, timeout=None):
            self.calls.append((params["key"], url.split("models/")[1].split(":")[0])); return self.seq.pop(0)
        def get(self, url, params=None, timeout=None):
            if params["key"] == "k1":
                names = ["gemini-3.9-flash"]
            else:
                names = ["gemini-3.8-flash"]
            return R(200, {"models": [{"name": "models/" + n, "supportedGenerationMethods": ["generateContent"]}
                                      for n in names]})
    http = H([ok('{"a": 1}'), ok('{"a": 2}')])
    c = GeminiClient(KeyPool("g", ["k1", "k2"], 0), ["x"], http)
    c.generate_json("x"); c.generate_json("x")
    assert dict(http.calls) == {"k1": "gemini-3.9-flash", "k2": "gemini-3.8-flash"}


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


def test_finish_times_and_merge():
    h = Hit(60, "mot " * 20, "literal", "target", "", 0.9, t_end=None)
    scan.finish_times([h], duration=3000)
    assert h.t_end == 60 + 20 / 2.5 + 1.5  # estimée d'après la longueur
    ok_ = Hit(10, "court", "literal", "target", "", 0.9, t_end=22)
    scan.finish_times([ok_]); assert ok_.t_end == 22  # valeur du LLM conservée
    bad = Hit(10, "court", "literal", "target", "", 0.9, t_end=5)  # fin avant début : corrigée
    scan.finish_times([bad]); assert bad.t_end > 10
    clamp = Hit(98, "x " * 30, "literal", "target", "", 0.9)
    scan.finish_times([clamp], duration=100); assert clamp.t_end == 100
    a = Hit(50, "q", "literal", "target", "", 0.9, t_end=58); b = Hit(54, "q", "literal", "target", "", 0.7, t_end=70)
    m = scan.dedupe_hits([a, b]); assert len(m) == 1 and m[0].t_end == 70  # étendue complète conservée


def test_plan_clips_adds_margins_and_filters():
    from quote_finder.clips import plan_clips
    data = {"videos": [{"title": "Le titre", "url": "https://y/v=1", "moments": [
        {"start_seconds": 105, "end_seconds": 118, "confidence": 0.95, "who_says": "other_named", "speaker_name": "Olivier Faure", "quote": "il ment"},
        {"start_seconds": 1, "end_seconds": 6, "confidence": 0.5, "who_says": "unknown"},
        {"start_seconds": 200, "end_seconds": 210, "confidence": 0.9, "who_says": "reported", "speaker_name": "X"}]}]}
    c = plan_clips(data, 2, 1, 0.8)
    assert [(x["start"], x["end"]) for x in c] == [(103, 119), (198, 211)]
    assert len(plan_clips(data, 2, 1, 0.8, only_own_voice=True)) == 1
    assert c[0]["name"].startswith("01_olivier-faure_le-titre_01m45")
    assert plan_clips(data, 5, 1, 0.0)[1]["start"] == 0  # jamais négatif


def test_openai_compat_switches_model_after_404_and_discovers():
    from quote_finder.llm import OpenAICompatClient
    seen = []

    class H:
        def post(self, url, json=None, headers=None, timeout=None):
            seen.append(json["model"])
            if json["model"] == "llama-3.3-70b-versatile":
                return R(404, '{"error":{"message":"The model `llama-3.3-70b-versatile` does not exist or you do not have access"}}')
            return ok_openai('{"ok": true}')
        def get(self, url, headers=None, timeout=None):
            return R(200, {"data": [{"id": "whisper-large-v3"}, {"id": "llama-guard-4-12b"}, {"id": "small-8b-instant"},
                                    {"id": "big-model-120b"}, {"id": "retired-70b", "active": False}]})

    def ok_openai(txt):
        return R(200, {"choices": [{"message": {"content": txt}}]})
    c = OpenAICompatClient(KeyPool("f", ["k"], 0), "https://x/v1", "llama-3.3-70b-versatile", H())
    assert c.generate_json("x") == {"ok": True}
    assert seen[0] == "llama-3.3-70b-versatile" and seen[1] == "big-model-120b"  # le plus gros modèle de chat actif


def test_gemini_lowers_thinking_then_falls_back_on_400():
    bodies = []

    class H(FakeHttp):
        def post(self, url, params=None, json=None, timeout=None):
            bodies.append(dict(json["generationConfig"]))
            return self.seq.pop(0)
    err = R(400, "Thinking level is not supported for this model")
    http = H([err, err, ok('{"a": 1}')])
    c = GeminiClient(KeyPool("g", ["k"], 0), "m", http)
    assert c.generate_json("x") == {"a": 1}
    assert bodies[0]["thinkingConfig"] == {"thinkingLevel": "low"}
    assert bodies[1]["thinkingConfig"] == {"thinkingBudget": 0}
    assert "thinkingConfig" not in bodies[2]


def test_hits_after_video_end_are_dropped():
    hits = [Hit(344, "x y z", "literal", "target", "", 0.9, t_end=246), Hit(217, "a b c", "literal", "target", "", 0.9, t_end=232)]
    scan.finish_times(hits, duration=246)
    assert [(h.t, h.t_end) for h in hits] == [(217, 232)]


def test_refine_finds_quote_far_from_ai_timestamp_and_handles_ellipsis():
    segs = [Segment(i * 4.0, "bla bla autre sujet") for i in range(200)]
    segs[126] = Segment(504.0, "switzerland wants to become")
    segs[127] = Segment(507.5, "more digitally independent what does")
    segs[128] = Segment(511.0, "that mean exactly")
    h = Hit(300, "Switzerland wants to become more digitally independent. What does that mean exactly?", "literal", "x", "", 0.9, t_end=310)
    scan.refine_times([h], segs)  # l'IA s'était trompée de 3 minutes
    assert h.t == 504.0 and h.t_end == segs[129].start and h.timing == "sous-titres"
    segs2 = [Segment(0, "intro"), Segment(10, "pour comprendre il faut écouter matthias"), Segment(14, "stürmer professeur"),
             Segment(18, "blabla long"), Segment(40, "les données de l'état sur un bureau"), Segment(44, "à washington voilà")]
    h2 = Hit(12, "Pour comprendre, il faut écouter Matthias Stürmer ... sur un bureau à Washington", "literal", "x", "", 0.9, t_end=50)
    scan.refine_times([h2], segs2)
    assert (h2.t, h2.t_end) == (10, 44 + 4.0) and h2.timing == "sous-titres"


def test_refine_does_not_guess_when_phrase_repeats_far_apart():
    segs = [Segment(i * 5.0, "x y z") for i in range(100)]
    segs[10] = Segment(50.0, "it's the best approach for both sides")
    segs[80] = Segment(400.0, "it's the best approach for both sides")
    h = Hit(250, "It's the best approach for both sides", "literal", "x", "", 0.9, t_end=255)
    scan.refine_times([h], segs)
    assert h.timing == "ia" and h.t == 250
