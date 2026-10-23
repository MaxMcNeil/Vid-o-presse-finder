"""Clients LLM gratuits : Gemini (clés en rotation) + repli OpenAI-compatible (Groq, Cerebras, OpenRouter)."""
from __future__ import annotations

import logging
import os
import re
import time

import requests

from . import cache
from .keys import KeyPool, keys_from_env
from .util import parse_json_loose

log = logging.getLogger("qf.llm")

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

PROVIDERS = {
    "groq": ("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
    "cerebras": ("https://api.cerebras.ai/v1", "llama-3.3-70b"),
    "openrouter": ("https://openrouter.ai/api/v1", "meta-llama/llama-3.3-70b-instruct:free"),
}


class LLMError(Exception):
    pass


class QuotaExhausted(LLMError):
    pass


DEFAULT_MODELS = ["gemini-3.8-flash", "gemini-flash-latest", "gemini-3-flash", "gemini-2.5-flash"]


def _model_rank(name: str):
    """Plus grand = préférable : version la plus récente, famille 'flash' (pas lite/image/tts...)."""
    n = name.replace("models/", "")
    if "flash" not in n or any(x in n for x in ("image", "tts", "live", "audio", "thinking", "embedding", "preview-")):
        return None
    m = re.search(r"gemini-(\d+(?:\.\d+)?)", n)
    return (float(m.group(1)) if m else 0.0) - (0.5 if "lite" in n else 0.0)


class GeminiClient:
    def __init__(self, pool: KeyPool, model: str | list[str], session=None, timeout: int = 180):
        self.models = [model] if isinstance(model, str) else list(model)
        self.pool, self.timeout = pool, timeout
        self.http = session or requests.Session()
        self._bad: set[str] = set()
        self._cool: dict[str, float] = {}
        self._overload: dict[str, int] = {}
        self._discovered = False

    @property
    def model(self) -> str:
        now = time.time()
        ok = [m for m in self.models if m not in self._bad]
        if not ok:
            raise LLMError("aucun modèle Gemini utilisable : définis la variable GEMINI_MODEL")
        for m in ok:
            if self._cool.get(m, 0) <= now:
                return m
        return min(ok, key=lambda m: self._cool.get(m, 0))  # tous surchargés : le moins pénalisé

    def _note_overload(self, key: str) -> None:
        """503 « forte demande » : après 2 échecs on met le modèle en pause 2 min et on passe au suivant."""
        m = self.model
        self._overload[m] = self._overload.get(m, 0) + 1
        if self._overload[m] >= 2:
            self._overload[m] = 0
            self._cool[m] = time.time() + 120
            if not self._discovered:
                self._discover(key)
            log.warning("Gemini %s surchargé, bascule sur %s", m, self.model)

    def _discover(self, key: str) -> None:
        """Demande à Google la liste des modèles accessibles à cette clé et ajoute les 'flash' les plus récents."""
        self._discovered = True
        try:
            r = self.http.get("https://generativelanguage.googleapis.com/v1beta/models",
                              params={"key": key, "pageSize": 200}, timeout=30)
            names = [m["name"].replace("models/", "") for m in r.json().get("models", [])
                     if "generateContent" in m.get("supportedGenerationMethods", [])]
        except (requests.RequestException, ValueError, KeyError, AttributeError):
            return
        ranked = sorted((n for n in names if _model_rank(n) is not None), key=lambda n: (-_model_rank(n), n))
        for n in ranked:
            if n not in self.models:
                self.models.insert(0, n) if n == ranked[0] else self.models.append(n)
        log.info("Modèles Gemini découverts : %s", ranked[:4])

    def generate_json(self, prompt: str, video_url: str | None = None, temperature: float = 0.1, inline_media=None,
                      max_attempts: int | None = None):
        """inline_media = (mime, octets) pour un enregistrement audio envoyé directement (< ~15 Mo)."""
        import base64
        parts = []
        if video_url:
            parts.append({"file_data": {"file_uri": video_url}})
        if inline_media:
            parts.append({"inline_data": {"mime_type": inline_media[0],
                                          "data": base64.b64encode(inline_media[1]).decode()}})
        parts.append({"text": prompt})
        body = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": temperature, "responseMimeType": "application/json"},
        }
        if (video_url or inline_media) and not getattr(self, "_no_res", False):
            # on n'a besoin que de la parole : résolution basse = ~3x moins de jetons de quota
            body["generationConfig"]["mediaResolution"] = "MEDIA_RESOLUTION_LOW"
        last = "aucune clé disponible"
        total = max_attempts or (max(8, 3 * len(self.pool._next_ok)) + len(self.models) + 6)
        for attempt in range(total):
            key = self.pool.acquire()
            if key is None:
                raise QuotaExhausted("toutes les clés Gemini sont épuisées")
            try:
                r = self.http.post(GEMINI_URL.format(model=self.model), params={"key": key},
                                   json=body, timeout=self.timeout)
            except requests.RequestException as e:
                last = str(e)
                time.sleep(2 + attempt)
                continue
            if r.status_code == 200:
                try:
                    data = r.json()
                    text = data["candidates"][0]["content"]["parts"][0]["text"]
                    return parse_json_loose(text)
                except (KeyError, IndexError, ValueError) as e:
                    last = f"réponse illisible: {e}"
                    continue
            msg = r.text[:400]
            last = f"HTTP {r.status_code}: {msg}"
            if r.status_code == 400 and "mediaResolution" in msg:
                self._no_res = True
                body["generationConfig"].pop("mediaResolution", None)
                continue
            if r.status_code in (404, 400) and re.search(r"no longer available|not found|is not supported|deprecated", msg, re.I) \
                    and "model" in msg.lower():
                log.warning("Modèle Gemini %s indisponible, on change de modèle", self.model)
                self._bad.add(self.model)
                if not self._discovered:
                    self._discover(key)
                continue
            if r.status_code == 429:
                if re.search(r"PerDay|per day|daily", msg, re.I):
                    log.warning("Gemini: quota journalier atteint pour une clé, rotation")
                    self.pool.exhaust(key)
                else:
                    m = re.search(r"retry in ([\d.]+)s", msg) or re.search(r'"retryDelay":\s*"(\d+)s"', msg)
                    self.pool.cooldown(key, float(m.group(1)) + 1 if m else 30)
                continue
            if r.status_code in (401, 403) and "API key" in msg or r.status_code == 403 and "denied" in msg.lower():
                self.pool.exhaust(key)
                continue
            if r.status_code >= 500:
                self._note_overload(key)
                time.sleep(min(3 + 2 * attempt, 20))
                continue
            raise LLMError(last)  # 400 : vidéo privée/indisponible, requête invalide...
        raise LLMError(last)


class OpenAICompatClient:
    def __init__(self, pool: KeyPool, base_url: str, model: str, session=None, timeout: int = 120):
        self.pool, self.base, self.model, self.timeout = pool, base_url.rstrip("/"), model, timeout
        self.http = session or requests.Session()

    def generate_json(self, prompt: str, video_url=None, temperature: float = 0.1):
        if video_url:
            raise LLMError("repli texte uniquement")
        body = {"model": self.model, "temperature": temperature,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": "Réponds uniquement par un objet JSON valide."},
                             {"role": "user", "content": prompt}]}
        last = ""
        for attempt in range(max(4, 2 * len(self.pool._next_ok))):
            key = self.pool.acquire()
            if key is None:
                raise QuotaExhausted("clés de repli épuisées")
            try:
                r = self.http.post(f"{self.base}/chat/completions", json=body, timeout=self.timeout,
                                   headers={"Authorization": f"Bearer {key}"})
            except requests.RequestException as e:
                last = str(e)
                time.sleep(2)
                continue
            if r.status_code == 200:
                try:
                    return parse_json_loose(r.json()["choices"][0]["message"]["content"])
                except (KeyError, IndexError, ValueError) as e:
                    last = f"réponse illisible: {e}"
                    continue
            last = f"HTTP {r.status_code}: {r.text[:300]}"
            if r.status_code == 429:
                self.pool.cooldown(key, 60)
                continue
            if r.status_code in (401, 403):
                self.pool.exhaust(key)
                continue
            if r.status_code >= 500:
                time.sleep(3)
                continue
            raise LLMError(last)
        raise LLMError(last)


class LLMRouter:
    """Essaie Gemini, puis le repli (texte seulement). Met les réponses en cache disque."""

    def __init__(self, gemini: GeminiClient | None, fallback: OpenAICompatClient | None):
        self.gemini, self.fallback = gemini, fallback

    @classmethod
    def from_env(cls) -> "LLMRouter":
        g = keys_from_env("GEMINI")
        forced = os.environ.get("GEMINI_MODEL")
        gem = GeminiClient(KeyPool("gemini", g, min_interval=float(os.environ.get("GEMINI_MIN_INTERVAL", "5"))),
                           [forced] + DEFAULT_MODELS if forced else DEFAULT_MODELS) if g else None
        fb = None
        prov = os.environ.get("FALLBACK_LLM_PROVIDER", "").lower()
        fkeys = keys_from_env("FALLBACK_LLM")
        if fkeys and prov:
            base, model = PROVIDERS.get(prov, ("", ""))
            base = os.environ.get("FALLBACK_LLM_BASE_URL") or base
            model = os.environ.get("FALLBACK_LLM_MODEL") or model
            if base and model:
                fb = OpenAICompatClient(KeyPool("fallback", fkeys, min_interval=2.5), base, model)
        return cls(gem, fb)

    @property
    def has_text(self) -> bool:
        return bool(self.gemini or self.fallback)

    @property
    def has_video(self) -> bool:
        return bool(self.gemini and self.gemini.pool.available())

    def generate_json(self, prompt: str, video_url: str | None = None, inline_media=None, media_key: str = ""):
        ck = f"{video_url or ''}|{media_key}\n{prompt}"
        hit = cache.get("llm", ck)
        if hit is not None:
            return hit
        err = None
        if self.gemini and self.gemini.pool.available():
            try:
                out = self.gemini.generate_json(prompt, video_url, inline_media=inline_media,
                                                max_attempts=(4 if self.fallback and not video_url and not inline_media else None))
                cache.put("llm", ck, out)
                return out
            except QuotaExhausted as e:
                err = e
            except LLMError as e:
                if video_url or inline_media:
                    raise
                err = e
                log.warning("Gemini a échoué (%s), tentative de repli", e)
        if self.fallback and not video_url and not inline_media:
            out = self.fallback.generate_json(prompt)
            cache.put("llm", ck, out)
            return out
        raise err or QuotaExhausted("aucun LLM disponible")
