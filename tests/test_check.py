"""Diagnostic : une surcharge de Google n'est jamais prise pour une clé morte."""
import requests

from quote_finder import check


class R:
    def __init__(self, code, data=None, text=""):
        self.status_code, self._d, self.text = code, data, text

    def json(self):
        return self._d


class H:
    def __init__(self, *resps):
        self.resps = list(resps)

    def get(self, *a, **k):
        r = self.resps.pop(0) if len(self.resps) > 1 else self.resps[0]
        if isinstance(r, Exception):
            raise r
        return r


def test_valid(monkeypatch):
    m = {"models": [{"supportedGenerationMethods": ["generateContent"]}] * 3}
    assert check.gemini_key_status("k", H(R(200, m))) == ("valid", "clé valide", 3)


def test_invalid_and_disabled():
    k, why, _ = check.gemini_key_status("k", H(R(400, text='{"reason": "API_KEY_INVALID"}')))
    assert k == "invalid" and "clé invalide" in why
    k, why, _ = check.gemini_key_status("k", H(R(403, text="SERVICE_DISABLED")))
    assert k == "invalid" and "non activée" in why


def test_overload_and_network_are_not_dead_keys(monkeypatch):
    monkeypatch.setattr(check.time, "sleep", lambda s: None)
    assert check.gemini_key_status("k", H(R(503)))[0] == "busy"
    assert check.gemini_key_status("k", H(requests.Timeout("lent")))[0] == "busy"
    assert check.gemini_key_status("k", H(R(503), R(200, {"models": []})))[0] == "valid"   # 2e essai réussi
    assert check.gemini_key_status("k", H(R(429)))[0] == "quota"
    assert check.gemini_key_status("k", H(R(200, None)))[0] == "valid"                     # réponse vide : clé OK
