"""Cascade des sous-titres : la plus fiable d'abord, bascule automatique, disjoncteur, jamais bloquant."""
from quote_finder import subtitles as s
from quote_finder.models import Segment

T = [s.Track([Segment(1.0, "bonjour à tous")], "fr", "auto", True)]


def _setup(monkeypatch, api, ytdlp, inv):
    s.reset()
    monkeypatch.setattr(s, "_api", lambda vid, langs, proxy="", translate_to="": api(vid))
    monkeypatch.setattr(s, "_ytdlp", lambda vid, langs, clients="", cookies=None, url="", timeout=90: ytdlp(vid, clients))
    monkeypatch.setattr(s, "_invidious", lambda vid, langs: inv(vid))
    monkeypatch.setattr(s, "cookies_file", lambda: None)
    monkeypatch.delenv("YT_PROXY_URL", raising=False)
    monkeypatch.setattr(s.time, "sleep", lambda x: None)


def blocked(*a):
    raise s.Blocked("IpBlocked")


def test_first_method_wins(monkeypatch):
    _setup(monkeypatch, lambda v: T, blocked, blocked)
    tracks, via = s.fetch("abc", ["fr"])
    assert tracks == T and via == "youtube-transcript-api"


def test_falls_back_in_order_until_one_works(monkeypatch):
    calls = []
    def ytdlp(v, clients):
        calls.append(clients)
        if not clients:
            raise s.Blocked("anti-robot")
        return T
    _setup(monkeypatch, blocked, ytdlp, blocked)
    tracks, via = s.fetch("abc", ["fr"])
    assert via == "yt-dlp (autres clients)" and calls == ["", s.YTDLP_CLIENTS]
    assert s.STATS.ok == {"yt-dlp (autres clients)": 1} and s.STATS.blocked["youtube-transcript-api"] == 1


def test_invidious_is_the_last_resort(monkeypatch):
    _setup(monkeypatch, blocked, blocked, lambda v: T)
    assert s.fetch("abc", ["fr"])[1] == "Invidious"


def test_refused_method_is_skipped_after_two_strikes(monkeypatch):
    n = {"api": 0}
    def api(v):
        n["api"] += 1
        raise s.Blocked("IpBlocked")
    _setup(monkeypatch, api, lambda v, c: T, blocked)
    for vid in ("a", "b", "c", "d"):
        s.fetch(vid, ["fr"])
    assert n["api"] == 2  # sauté ensuite : pas de temps perdu


def test_no_subtitles_stops_the_cascade(monkeypatch):
    def api(v):
        raise s.NoSubtitles("TranscriptsDisabled")
    seen = []
    _setup(monkeypatch, api, lambda v, c: seen.append(1) or T, blocked)
    assert s.fetch("abc", ["fr"]) == ([], "youtube-transcript-api") and not seen


def test_everything_refused_raises_blocked_for_a_later_retry(monkeypatch):
    _setup(monkeypatch, blocked, blocked, blocked)
    try:
        s.fetch("abc", ["fr"])
        raise AssertionError("Blocked attendu")
    except s.Blocked:
        pass
    assert s.STATS.failed == 1


def test_a_crashing_method_never_breaks_the_next(monkeypatch):
    def api(v):
        raise RuntimeError("bug interne")
    _setup(monkeypatch, api, lambda v, c: T, blocked)
    assert s.fetch("abc", ["fr"])[1] == "yt-dlp"


def test_cookies_and_proxy_methods_appear_only_when_configured(monkeypatch, tmp_path):
    s.reset()
    monkeypatch.delenv("YT_PROXY_URL", raising=False)
    monkeypatch.setattr(s, "cookies_file", lambda: None)
    names = [n for n, _ in s.methods(["fr"])]
    assert names == ["youtube-transcript-api", "yt-dlp", "yt-dlp (autres clients)", "Invidious"]
    monkeypatch.setenv("YT_PROXY_URL", "http://p:1")
    ck = tmp_path / "c.txt"
    ck.write_text("x" * 100)
    monkeypatch.setattr(s, "cookies_file", lambda: ck)
    names = [n for n, _ in s.methods(["fr"])]
    assert names[1] == "youtube-transcript-api + proxy" and "yt-dlp + cookies" in names


def test_vtt_parser_keeps_exact_times():
    raw = "WEBVTT\n\n00:00:00.500 --> 00:00:02.600\nBienvenue\n\n00:01:02.600 --> 00:01:06.000\n<c>la suite</c>\n"
    segs = s.parse_vtt_srt(raw)
    assert [(x.start, x.text) for x in segs] == [(0.5, "Bienvenue"), (62.6, "la suite")]
