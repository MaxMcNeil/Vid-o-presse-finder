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


class GeminiClient:
    def __init__(self, pool: KeyPool, model: str, session=None, timeout: int = 180):
        self.pool, self.model, self.timeout = pool, model, timeout
        self.http = session or requests.Session()

    def generate_json(self, prompt: str, video_url: str | None = None, temperature: float = 0.1):
        parts = []
        if video_url:
            parts.append({"file_data": {"file_uri": video_url}})
        parts.append({"text": prompt})
        body = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": temperature, "responseMimeType": "application/json"},
        }
        last = "aucune clé disponible"
        for attempt in range(max(6, 3 * len(self.pool._next_ok))):
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
                time.sleep(3 + 2 * attempt)
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
        gem = GeminiClient(KeyPool("gemini", g, min_interval=float(os.environ.get("GEMINI_MIN_INTERVAL", "5"))),
                           (os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash")) if g else None
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

    def generate_json(self, prompt: str, video_url: str | None = None):
        ck = f"{video_url or ''}\n{prompt}"
        hit = cache.get("llm", ck)
        if hit is not None:
            return hit
        err = None
        if self.gemini and self.gemini.pool.available():
            try:
                out = self.gemini.generate_json(prompt, video_url)
                cache.put("llm", ck, out)
                return out
            except QuotaExhausted as e:
                err = e
            except LLMError as e:
                if video_url:
                    raise
                err = e
                log.warning("Gemini a échoué (%s), tentative de repli", e)
        if self.fallback and not video_url:
            out = self.fallback.generate_json(prompt)
            cache.put("llm", ck, out)
            return out
        raise err or QuotaExhausted("aucun LLM disponible")
