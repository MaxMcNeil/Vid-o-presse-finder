"""Un 401/403 sur UN modèle ne doit jamais faire déclarer la clé morte si le fournisseur confirme qu'elle est valide."""
from quote_finder import llm


class R:
    def __init__(self, c):
        self.status_code = c


class H:
    def __init__(self, c):
        self.c, self.urls = c, []

    def get(self, url, **k):
        self.urls.append(url)
        if isinstance(self.c, Exception):
            raise self.c
        return R(self.c)


def test_cle_valide_gemini_et_relais():
    llm._KEY_OK.clear()
    assert llm.key_ok("a", http=H(200))
    assert not llm.key_ok("b", http=H(400))
    h = H(200)
    assert llm.key_ok("c", "https://openrouter.ai/api/v1", h) and h.urls[0].endswith("/key")
    h = H(403)
    assert not llm.key_ok("d", "https://api.groq.com/openai/v1", h) and h.urls[0].endswith("/models")


def test_doute_reseau_ne_condamne_pas():
    llm._KEY_OK.clear()
    assert llm.key_ok("e", http=H(OSError("réseau")))
