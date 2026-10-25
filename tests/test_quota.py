import json
import time

import pytest

from quote_finder import quota
from quote_finder.keys import KeyPool


class R:
    def __init__(self, code, body, headers=None):
        self.status_code, self._b, self.headers = code, body, headers or {}
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        return self._b if not isinstance(self._b, str) else json.loads(self._b)


@pytest.fixture
def st(tmp_path, monkeypatch):
    monkeypatch.setenv("QF_QUOTA_FILE", str(tmp_path / "quota.json"))
    return quota.reset_state()


def test_exhausted_key_is_skipped_on_next_run(st, tmp_path):
    p = KeyPool("gemini", ["k1", "k2", "k3"], 0, persist=True)
    p.exhaust("k1")
    st.save()
    quota.reset_state()  # nouveau lancement
    p2 = KeyPool("gemini", ["k1", "k2", "k3"], 0, persist=True)
    assert p2.available() == 2 and p2.acquire() == "k2"
    md = quota.state().markdown()
    assert "Gemini clé 1" in md and "épuisée" in md and "k1" not in md.replace("clé 1", "")  # jamais la clé en clair


def test_parallel_jobs_start_on_different_keys(st, monkeypatch):
    monkeypatch.setenv("QF_KEY_OFFSET", "2")
    assert KeyPool("youtube", ["a", "b", "c", "d", "e"], 0, persist=True).acquire() == "c"


def test_youtube_units_are_counted_and_empty_key_skipped(st):
    from quote_finder.sources import youtube
    seen = []

    class H:
        def get(self, url, params=None, timeout=None):
            seen.append(params["key"])
            return R(200, {"items": []})
    p = KeyPool("youtube", ["y1", "y2"], 0, persist=True)
    quota.state().use("youtube", "y1", quota.YT_DAILY_UNITS - 50)  # y1 presque vide
    youtube._call(p, "search", {"q": "x"}, H())
    assert seen == ["y2"] and quota.state().units_left("youtube", "y2") == quota.YT_DAILY_UNITS - 100


def test_gemini_daily_quota_moves_to_next_model_then_persists(st):
    from quote_finder.llm import GeminiClient

    class H:
        calls = []

        def get(self, url, params=None, timeout=None):
            return R(200, {"models": [{"name": f"models/{n}", "supportedGenerationMethods": ["generateContent"]}
                                      for n in ("gemini-9-flash", "gemini-9-flash-lite", "gemma-3-27b-it")]})

        def post(self, url, params=None, json=None, timeout=None):
            m = url.split("models/")[1].split(":")[0]
            self.calls.append(m)
            if m == "gemini-9-flash":
                return R(429, "Quota exceeded for GenerateRequestsPerDayPerProjectPerModel-FreeTier")
            return R(200, {"candidates": [{"content": {"parts": [{"text": '{"ok": 1}'}]}}]})
    h = H()
    c = GeminiClient(KeyPool("gemini", ["g1"], 0, persist=True), ["gemini-9-flash"], h)
    assert c.generate_json("x") == {"ok": 1} and h.calls == ["gemini-9-flash", "gemini-9-flash-lite"]
    assert quota.state().model_until("gemini", "g1", "gemini-9-flash") > time.time()
    # vidéo : Gemma (texte seul) n'est jamais utilisé
    assert c._pick("g1", media=True)[0] == "gemini-9-flash-lite"


def test_gemma_without_json_mode_is_handled(st):
    from quote_finder.llm import GeminiClient
    bodies = []

    class H:
        def get(self, *a, **k):
            return R(200, {"models": [{"name": "models/gemma-3-27b-it", "supportedGenerationMethods": ["generateContent"]}]})

        def post(self, url, params=None, json=None, timeout=None):
            bodies.append(json["generationConfig"])
            if "responseMimeType" in json["generationConfig"]:
                return R(400, "JSON mode is not enabled for models/gemma-3-27b-it")
            return R(200, {"candidates": [{"content": {"parts": [{"text": '```json\n{"ok": 2}\n```'}]}}]})
    c = GeminiClient(KeyPool("gemini", ["g"], 0, persist=True), ["x"], H())
    assert c.generate_json("x") == {"ok": 2} and "thinkingConfig" not in bodies[0]


def test_router_chain_gemini_then_groq_then_cerebras(st, monkeypatch):
    from quote_finder import llm as L
    monkeypatch.setenv("GROQ_API_KEYS", "q1")
    monkeypatch.setenv("CEREBRAS_API_KEYS", "c1")
    monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
    r = L.LLMRouter.from_env()
    assert [f.name for f in r.fallbacks] == ["groq", "cerebras"]

    class Dead:
        def post(self, url, json=None, headers=None, timeout=None):
            if "groq" in url:
                return R(429, "Rate limit reached: tokens per day (TPD) exceeded")
            return R(200, {"choices": [{"message": {"content": '{"ok": 3}'}}]})
    for f in r.fallbacks:
        f.http = Dead()
    assert r.generate_json("bonjour unique 123") == {"ok": 3}
    assert quota.state().key_until("groq", "q1") > time.time()  # groq épuisé pour la journée, mémorisé


def test_merge_parallel_states(st, tmp_path):
    base = tmp_path / "state" / "quota.json"
    s0 = quota.QuotaState(base)
    s0.use("youtube", "y", 200)
    s0.save()
    a, b = quota.QuotaState(base), quota.QuotaState(base)
    a.path, b.path = tmp_path / "a.json", tmp_path / "b.json"
    a.use("youtube", "y", 100)
    b.use("youtube", "y", 300)
    b.exhaust_key("gemini", "g")
    a.save(); b.save()
    m = quota.merge(base, [a.path, b.path])
    assert m.units_left("youtube", "y") == quota.YT_DAILY_UNITS - 600
    assert m.key_until("gemini", "g") > time.time()


def test_next_reset_is_pacific_midnight():
    import datetime as dt
    r = dt.datetime.fromtimestamp(quota.next_reset(), quota.PACIFIC)
    assert (r.hour, r.minute) == (0, 1) and r.timestamp() > time.time()


def test_groq_key_in_old_secret_is_recognised_without_variable(st, monkeypatch):
    from quote_finder import llm as L
    for v in ("FALLBACK_LLM_PROVIDER", "GROQ_API_KEYS", "CEREBRAS_API_KEYS", "OPENROUTER_API_KEYS"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("FALLBACK_LLM_KEYS", "gsk_abc123\nsk-or-v1-xyz")
    r = L.LLMRouter.from_env()
    assert [(f.name, list(f.pool._next_ok)) for f in r.fallbacks] == [("groq", ["gsk_abc123"]), ("openrouter", ["sk-or-v1-xyz"])]


def test_daily_quota_detected_even_at_end_of_long_google_error(st):
    """Réponse réelle du 25/10 : « You exceeded your current quota… » avec le type de quota (PerDay) après 400 caractères."""
    from quote_finder.llm import GeminiClient
    body = ('{"error":{"code":429,"message":"You exceeded your current quota, please check your plan and billing details. '
            + "x" * 500 + ' quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier"}}')

    class H:
        calls = []

        def get(self, *a, **k):
            return R(200, {"models": [{"name": "models/gemini-9-flash", "supportedGenerationMethods": ["generateContent"]},
                                      {"name": "models/gemini-9-flash-lite", "supportedGenerationMethods": ["generateContent"]}]})

        def post(self, url, params=None, json=None, timeout=None):
            m = url.split("models/")[1].split(":")[0]
            self.calls.append(m)
            if m == "gemini-9-flash":
                return R(429, body)
            return R(200, {"candidates": [{"content": {"parts": [{"text": '{"ok": 1}'}]}}]})
    h = H()
    c = GeminiClient(KeyPool("gemini", ["g1"], 0, persist=True), ["x"], h)
    assert c.generate_json("x") == {"ok": 1} and h.calls == ["gemini-9-flash", "gemini-9-flash-lite"]
    assert quota.state().model_until("gemini", "g1", "gemini-9-flash") > time.time() + 3600  # jusqu'à la remise à zéro
