"""Telegram / Reddit / appui-contre : sans réseau, cas normaux ET extrêmes."""
import json
import os
import time
from types import SimpleNamespace as NS

import pytest

from quote_finder import social


def plan(**k):
    d = dict(target_aliases=["Bardella"], term_groups=[["immigration", "migrants"], ["chiffres"]],
             site_query="Bardella immigration", news_queries=[], posts=[])
    d.update(k)
    return NS(**d)


def opts(**k):
    d = dict(target="Jordan Bardella", claim="immigration chiffres", target_type="personne", since="")
    d.update(k)
    return NS(**d)


class Resp:
    def __init__(self, code=200, data=None, content=b""):
        self.status_code, self._d, self.content = code, data, content
        self.ok = code < 400

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        if self._d is None:
            raise ValueError("pas de json")
        return self._d


class Http:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, **k):
        self.calls.append(url)
        for key, r in self.routes.items():
            if key in url:
                return r(url, k) if callable(r) else r
        return Resp(404)


def red_json(n=3, score=10):
    return {"data": {"children": [{"data": {"title": f"Bardella et l'immigration : les chiffres {i}", "selftext": "",
                                            "subreddit": "france", "permalink": f"/r/france/comments/{i}/x/",
                                            "created_utc": time.time() - 86400, "score": score, "num_comments": 4}}
                                  for i in range(n)]}}


def test_queries_and_words_dedup():
    q = social.queries(plan(), opts())
    assert q and len(q) <= 3 and len({x.lower() for x in q}) == len(q)
    w = social.words(plan(), opts())
    assert "Bardella" in w and len(w) <= 3


def test_queries_survive_empty_plan():
    assert social.queries(plan(target_aliases=[], term_groups=[], site_query=""), opts()) == ["Jordan Bardella"]


def test_relevant_rules():
    p, o = plan(), opts()
    assert social.relevant("Bardella ment sur l'immigration, voici les vrais chiffres officiels", p, o)
    assert not social.relevant("court", p, o)
    assert not social.relevant("Un long message sur l'immigration en Europe sans nommer personne du tout", p, o)
    assert social.relevant("Un long message sur l'immigration en Europe sans nommer personne du tout", p,
                           opts(target_type="sujet"))


def test_reddit_ok_filters_low_score_and_nsfw():
    d = red_json(3)
    d["data"]["children"][0]["data"]["score"] = 1
    d["data"]["children"][1]["data"]["over_18"] = True
    out = social.reddit(["Bardella immigration"], http=Http({"search.json": Resp(200, d)}))
    assert len(out) == 1 and out[0]["channel"] == "r/france" and out[0]["url"].startswith("https://www.reddit.com/r/")


def test_reddit_blocked_everywhere_is_reported_not_raised():
    social.reset()
    out = social.reddit(["x"], http=Http({"search.json": Resp(403), "search.rss": Resp(403)}))
    assert out == [] and "bloqué" in social.STATUS["reddit"] and "403" in social.STATUS["reddit"]


def test_reddit_json_blocked_rss_ok():
    atom = (b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Bardella immigration chiffres'
            b'</title><link href="https://www.reddit.com/r/france/comments/abc/x/"/><category term="france"/>'
            b'<updated>2026-10-01T00:00:00Z</updated></entry></feed>')
    out = social.reddit(["x"], http=Http({"search.json": Resp(429), "search.rss": Resp(200, content=atom)}))
    assert len(out) == 1 and out[0]["date"] == "2026-10-01"


def test_reddit_garbage_and_exceptions():
    import requests

    def boom(url, k):
        raise requests.ConnectionError("dns")
    social.reset()
    assert social.reddit(["x"], http=Http({"search.json": Resp(200, None), "search.rss": Resp(200, content=b"<<<")})) == []
    assert social.reddit(["x"], http=Http({"reddit.com": boom})) == []
    assert "bloqué" in social.STATUS["reddit"]


@pytest.mark.parametrize("env,want", [
    ({}, "non configuré"),
    ({"TG_API_ID": "123"}, "incomplète"),
    ({"TG_API_ID": "abc", "TG_API_HASH": "0" * 32, "TG_SESSION": "s"}, "TG_API_ID invalide"),
    ({"TG_API_ID": "123", "TG_API_HASH": "court", "TG_SESSION": "s"}, "TG_API_HASH invalide"),
])
def test_tg_config_errors(monkeypatch, env, want):
    for k in ("TG_API_ID", "TG_API_HASH", "TG_SESSION"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert want in social.tg_config()


def test_tg_config_ok(monkeypatch):
    monkeypatch.setenv("TG_API_ID", " 12345 ")
    monkeypatch.setenv("TG_API_HASH", "a" * 32)
    monkeypatch.setenv("TG_SESSION", "1Aabc")
    assert social.tg_config() == (12345, "a" * 32, "1Aabc")


def test_telegram_not_configured_never_raises(monkeypatch):
    for k in ("TG_API_ID", "TG_API_HASH", "TG_SESSION"):
        monkeypatch.delenv(k, raising=False)
    social.reset()
    assert social.telegram(plan(), opts(), 60) == [] and "non configuré" in social.STATUS["telegram"]


@pytest.mark.parametrize("name,frag", [("AuthKeyDuplicatedError", "régénère"), ("AuthKeyUnregisteredError", "régénère"),
                                       ("FloodWaitError", "limite"), ("TimeoutError", "temps imparti"),
                                       ("WeirdError", "erreur WeirdError")])
def test_tg_error_messages(name, frag):
    e = type(name, (Exception,), {})("x")
    assert frag in social._tg_error(e, 2)


def test_telegram_search_with_fake_client(monkeypatch):
    """Faux client Telethon : chaînes trouvées, messages filtrés (pertinence, date), lien t.me correct."""
    import datetime as dt
    import telethon
    from telethon.tl.types import Channel
    monkeypatch.setenv("TG_API_ID", "1")
    monkeypatch.setenv("TG_API_HASH", "b" * 32)
    monkeypatch.setenv("TG_SESSION", "x")
    for k in ("GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(k, raising=False)

    def chan(i, name, n):
        c = Channel.__new__(Channel)
        c.id, c.username, c.broadcast, c.participants_count, c.scam, c.fake = i, name, True, n, False, False
        return c

    class Msg:
        def __init__(self, i, text, days=1):
            self.id, self.message, self.views, self.video, self.photo = i, text, 100 * i, None, None
            self.date = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)

    class Client:
        def __init__(self, *a, **k):
            pass

        async def connect(self):
            pass

        async def disconnect(self):
            pass

        async def is_user_authorized(self):
            return True

        async def __call__(self, req):
            return NS(chats=[chan(1, "infos_fr", 5000), chan(2, "petit", 10), NS(id=3)])

        def iter_messages(self, ch, search=None, limit=6):
            async def gen():
                yield Msg(1, "Bardella sur l'immigration : les chiffres réels publiés aujourd'hui par l'Insee")
                yield Msg(2, "message sans rapport avec le sujet mais assez long pour passer la longueur")
                yield Msg(3, "Bardella immigration chiffres très anciens et périmés de plusieurs années", days=4000)
            return gen()
    monkeypatch.setattr(telethon, "TelegramClient", Client)
    import telethon.sessions
    monkeypatch.setattr(telethon.sessions, "StringSession", lambda s: s)
    social.reset()
    out = social.telegram(plan(), opts(since="2025-01-01"), 60)
    assert {x["url"] for x in out} == {"https://t.me/infos_fr/1", "https://t.me/petit/1"}
    assert social.STATUS["telegram"].startswith("ok : 2 message")


def test_telegram_unauthorized_session_is_loud(monkeypatch):
    import telethon
    monkeypatch.setenv("TG_API_ID", "1")
    monkeypatch.setenv("TG_API_HASH", "b" * 32)
    monkeypatch.setenv("TG_SESSION", "x")
    for k in ("GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(k, raising=False)

    class Client:
        def __init__(self, *a, **k):
            pass

        async def connect(self):
            pass

        async def disconnect(self):
            pass

        async def is_user_authorized(self):
            return False
    monkeypatch.setattr(telethon, "TelegramClient", Client)
    import telethon.sessions
    monkeypatch.setattr(telethon.sessions, "StringSession", lambda s: s)
    social.reset()
    assert social.telegram(plan(), opts(), 30) == []
    assert "régénère TG_SESSION" in social.STATUS["telegram"]


def test_telegram_hang_is_bounded(monkeypatch):
    """Telegram qui ne répond plus : on rend la main dans le temps imparti."""
    import asyncio
    import telethon
    monkeypatch.setenv("TG_API_ID", "1")
    monkeypatch.setenv("TG_API_HASH", "b" * 32)
    monkeypatch.setenv("TG_SESSION", "x")
    for k in ("GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(k, raising=False)

    class Client:
        def __init__(self, *a, **k):
            pass

        async def connect(self):
            await asyncio.sleep(3600)

        async def disconnect(self):
            pass
    monkeypatch.setattr(telethon, "TelegramClient", Client)
    import telethon.sessions
    monkeypatch.setattr(telethon.sessions, "StringSession", lambda s: s)
    social.reset()
    t = time.time()
    assert social.telegram(plan(), opts(), 40) == []   # budget 40 s → 30 s max dans Telegram
    assert time.time() - t < 45 and "temps imparti" in social.STATUS["telegram"]


class FakeGH:
    """API GitHub minimale (étiquettes) pour tester le verrou entre machines."""
    def __init__(self, held_age=None):
        self.refs, self.tags, self.n = {}, {}, 0
        if held_age is not None:
            self.tags["t0"] = json.dumps({"t": time.time() - held_age})
            self.refs["verrou-telegram"] = "t0"

    def get(self, url, **k):
        if url.endswith("/git/ref/tags/verrou-telegram"):
            if "verrou-telegram" not in self.refs:
                return Resp(404)
            return Resp(200, {"object": {"type": "tag", "sha": self.refs["verrou-telegram"]}})
        if "/git/tags/" in url:
            return Resp(200, {"message": self.tags[url.rsplit("/", 1)[1]]})
        return Resp(404)

    def post(self, url, json=None, **k):
        if url.endswith("/git/tags"):
            self.n += 1
            sha = f"t{self.n}"
            self.tags[sha] = json["message"]
            return Resp(201, {"sha": sha})
        if url.endswith("/git/refs"):
            if "verrou-telegram" in self.refs:
                return Resp(422, {})
            self.refs["verrou-telegram"] = json["sha"]
            return Resp(201, {})
        return Resp(404)

    def delete(self, url, **k):
        self.refs.pop("verrou-telegram", None)
        return Resp(204)


def _with_gh(monkeypatch, gh):
    monkeypatch.setenv("GITHUB_SHA", "abc")
    monkeypatch.setattr(social, "_gh", lambda: (gh, "https://api/repos/o/r"))


def test_lock_acquire_and_release(monkeypatch):
    gh = FakeGH()
    _with_gh(monkeypatch, gh)
    with social.telegram_lock(5) as lk:
        assert lk["ok"] and "verrou-telegram" in gh.refs
    assert "verrou-telegram" not in gh.refs


def test_lock_busy_gives_up_in_time(monkeypatch):
    gh = FakeGH(held_age=10)
    _with_gh(monkeypatch, gh)
    monkeypatch.setattr(social.time, "sleep", lambda s: None)
    t = time.time()
    with social.telegram_lock(0.5) as lk:
        assert not lk["ok"]
    assert time.time() - t < 5 and gh.refs["verrou-telegram"] == "t0"   # le verrou d'un autre n'est jamais retiré


def test_lock_stale_is_taken_over(monkeypatch):
    gh = FakeGH(held_age=social.LOCK_STALE + 60)
    _with_gh(monkeypatch, gh)
    with social.telegram_lock(5) as lk:
        assert lk["ok"] and gh.refs["verrou-telegram"] != "t0"


def test_lock_kept_when_session_may_still_be_open(monkeypatch):
    gh = FakeGH()
    _with_gh(monkeypatch, gh)
    with social.telegram_lock(5) as lk:
        lk["keep"] = True
    assert "verrou-telegram" in gh.refs


def test_lock_api_down_fails_closed(monkeypatch):
    class Down(FakeGH):
        def get(self, url, **k):
            r = Resp(500)
            r.raise_for_status = lambda: (_ for _ in ()).throw(RuntimeError("500"))
            return r
    _with_gh(monkeypatch, Down())
    with pytest.raises(RuntimeError):
        with social.telegram_lock(1):
            pass


def test_search_disabled_and_never_raises(monkeypatch):
    monkeypatch.setenv("QF_SOCIAL", "0")
    assert social.search(plan(), opts()) == [] and "désactivé" in social.STATUS["telegram"]
    monkeypatch.setenv("QF_SOCIAL", "1")
    monkeypatch.setattr(social, "reddit", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(social, "telegram", lambda *a, **k: (_ for _ in ()).throw(KeyError("y")))
    assert social.search(plan(), opts()) == []
    assert "erreur" in social.STATUS["reddit"] and "erreur" in social.STATUS["telegram"]


def test_classify_ok_partial_and_failure():
    items = [{"text": "a"}, {"text": "b"}, {"text": "c"}]
    llm = NS(generate_json=lambda p: {"positions": [{"i": 0, "p": "appui"}, {"i": "1", "p": "CONTRE"}, {"i": 9, "p": "x"}]})
    social.reset()
    social.classify(llm, opts(), items)
    assert [x["position"] for x in items] == ["appui", "contre", "?"]
    assert "1 élément" in social.STATUS["positions"]

    def boom(p):
        raise RuntimeError("quota")
    social.classify(NS(generate_json=boom), opts(), items)
    assert all(x["position"] == "?" for x in items) and "non classées" in social.STATUS["positions"]
    social.classify(NS(generate_json=lambda p: "pas du json"), opts(), items)
    social.classify(NS(generate_json=lambda p: {"positions": [{"p": "appui"}]}), opts(), items)
    social.classify(NS(generate_json=lambda p: {}), opts(), [])


def test_bad_session_string_is_loud(monkeypatch):
    monkeypatch.setenv("TG_API_ID", "1")
    monkeypatch.setenv("TG_API_HASH", "b" * 32)
    monkeypatch.setenv("TG_SESSION", "pas-une-session")
    for k in ("GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    social.reset()
    assert social.telegram(plan(), opts(), 30) == []
    assert "illisible" in social.STATUS["telegram"] and "régénère" in social.STATUS["telegram"]
