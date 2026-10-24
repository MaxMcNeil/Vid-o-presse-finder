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
    """Chaque clé a ses propres modèles accessibles (découverts avec CETTE clé) ; les quotas gratuits sont comptés par
    (clé, modèle) : un modèle à bout de quota sur une clé n'empêche pas d'utiliser un autre modèle sur la même clé."""

    def __init__(self, pool: KeyPool, model: str | list[str], session=None, timeout: int = 180, preferred=None):
        self.models = [model] if isinstance(model, str) else list(model)  # secours si la découverte échoue
        self.preferred = list(preferred or [])  # choix explicite de l'utilisateur (GEMINI_MODEL) : essayé en premier
        self.pool, self.timeout = pool, timeout
        self.http = session or requests.Session()
        self._key_models: dict[str, list[str]] = {}
        self._until: dict[tuple[str, str], float] = {}   # (clé, modèle) -> indisponible jusqu'à
        self._bad: set[tuple[str, str]] = set()          # (clé, modèle) -> inaccessible
        self._cool: dict[str, float] = {}                # modèle -> surchargé jusqu'à (toutes clés)
        self._overload: dict[str, int] = {}
        self._think: dict[str, int] = {}                 # 0 niveau bas, 1 budget 0, 2 défaut du modèle
        self._no_res = False
        self.last_model = (self.preferred or self.models or ["?"])[0]
        self.deadline: float | None = None

    @property
    def model(self) -> str:
        return self.last_model

    def _models_for(self, key: str) -> list[str]:
        if key in self._key_models:
            return self._key_models[key]
        names: list[str] = []
        try:
            r = self.http.get("https://generativelanguage.googleapis.com/v1beta/models",
                              params={"key": key, "pageSize": 200}, timeout=30)
            names = [m["name"].replace("models/", "") for m in r.json().get("models", [])
                     if "generateContent" in m.get("supportedGenerationMethods", [])]
        except (requests.RequestException, ValueError, KeyError, AttributeError, TypeError):
            pass
        ranked = sorted((n for n in names if _model_rank(n) is not None), key=lambda n: (-_model_rank(n), n))
        if ranked:
            lst = [m for m in self.preferred if m in ranked] + [n for n in ranked if n not in self.preferred]
        else:  # découverte impossible : on tente les noms connus
            lst = list(dict.fromkeys(self.preferred + self.models))
        self._key_models[key] = lst
        log.info("Gemini clé …%s : %d modèle(s) accessible(s), meilleur : %s", key[-4:], len(lst), lst[:3])
        return lst

    def _pick(self, key: str) -> tuple[str | None, float]:
        """Meilleur modèle disponible pour cette clé ; sinon (None, secondes avant le prochain retour possible)."""
        now = time.time()
        soon = float("inf")
        for m in self._models_for(key):
            if (key, m) in self._bad:
                continue
            free_at = max(self._until.get((key, m), 0), self._cool.get(m, 0))
            if free_at <= now:
                return m, 0.0
            soon = min(soon, free_at - now)
        return None, soon

    def _note_overload(self, m: str) -> None:
        """503 « forte demande » : après 2 échecs le modèle est mis en pause 2 min (pour toutes les clés)."""
        self._overload[m] = self._overload.get(m, 0) + 1
        if self._overload[m] >= 2:
            self._overload[m] = 0
            self._cool[m] = time.time() + 120
            log.warning("Gemini %s surchargé : pause 2 min, on utilise d'autres modèles", m)

    def generate_json(self, prompt: str, video_url: str | None = None, temperature: float = 0.1, inline_media=None,
                      max_attempts: int | None = None, until: float | None = None):
        """inline_media = (mime, octets) pour un enregistrement audio envoyé directement (< ~15 Mo)."""
        import base64
        parts = []
        if video_url:
            parts.append({"file_data": {"file_uri": video_url}})
        if inline_media:
            parts.append({"inline_data": {"mime_type": inline_media[0],
                                          "data": base64.b64encode(inline_media[1]).decode()}})
        parts.append({"text": prompt})
        media = bool(video_url or inline_media)
        last = "aucune clé disponible"
        total = max_attempts or (max(10, 4 * len(self.pool._next_ok)) + 10)
        for attempt in range(total):
            lim = min(x for x in (self.deadline, until, float("inf")) if x)
            if time.time() > lim:
                raise QuotaExhausted("temps imparti écoulé")
            key = self.pool.acquire()
            if key is None:
                raise QuotaExhausted(f"toutes les clés Gemini sont épuisées ou refusées (dernière erreur : {last[:140]})")
            m, soon = self._pick(key)
            if m is None:
                if soon <= 150:  # pause courte (surcharge, limite par minute) : on patiente
                    time.sleep(min(soon + 0.5, 20))
                else:            # quota du jour épuisé pour tous les modèles de cette clé
                    self.pool.cooldown(key, soon)
                continue
            self.last_model = m
            body = {"contents": [{"role": "user", "parts": parts}],
                    "generationConfig": {"temperature": temperature, "responseMimeType": "application/json"}}
            think = self._think.get(m, 0)
            if think == 0:
                body["generationConfig"]["thinkingConfig"] = {"thinkingLevel": "low"}
            elif think == 1:
                body["generationConfig"]["thinkingConfig"] = {"thinkingBudget": 0}
            if media and not self._no_res:
                body["generationConfig"]["mediaResolution"] = "MEDIA_RESOLUTION_LOW"  # la parole suffit : ~3x moins de jetons
            try:
                r = self.http.post(GEMINI_URL.format(model=m), params={"key": key}, json=body,
                                   timeout=max(10.0, min(self.timeout, lim - time.time())))
            except requests.RequestException as e:
                last = str(e)
                time.sleep(min(2 + attempt, 8))
                continue
            if r.status_code == 200:
                try:
                    out = parse_json_loose(r.json()["candidates"][0]["content"]["parts"][0]["text"])
                    self._overload[m] = 0
                    return out
                except (KeyError, IndexError, ValueError, TypeError) as e:
                    last = f"réponse illisible: {e}"
                    continue
            msg = r.text[:400]
            last = f"HTTP {r.status_code} ({m}): {msg}"
            low = msg.lower()
            if r.status_code == 400 and "thinking" in low and think < 2:
                self._think[m] = think + 1
                continue
            if r.status_code == 400 and "mediaresolution" in low:
                self._no_res = True
                continue
            if r.status_code in (400, 404) and "model" in low and re.search(
                    r"no longer available|not found|is not supported|deprecated|does not exist", low):
                log.warning("Gemini : %s inaccessible avec la clé …%s, on passe à un autre modèle", m, key[-4:])
                self._bad.add((key, m))
                continue
            if r.status_code == 429:
                if re.search(r"perday|per day|daily", low):
                    log.warning("Gemini : quota du jour de %s atteint (clé …%s) → autre modèle ou autre clé", m, key[-4:])
                    self._until[(key, m)] = time.time() + 86400
                else:
                    d = re.search(r"retry in ([\d.]+)s", msg) or re.search(r'"retryDelay":\s*"(\d+)s"', msg)
                    self._until[(key, m)] = time.time() + (float(d.group(1)) + 1 if d else 30)
                continue
            if r.status_code in (401, 403):
                if "api key" in low or "api_key" in low:
                    self.pool.exhaust(key)            # clé invalide ou révoquée
                else:
                    self._bad.add((key, m))           # accès refusé à CE modèle seulement
                continue
            if r.status_code >= 500:
                self._note_overload(m)
                time.sleep(min(2 + attempt, 8))
                continue
            raise LLMError(last)  # 400 : vidéo privée/indisponible, requête invalide...
        raise LLMError(last)


def _open_rank(mid: str, meta: dict | None = None) -> float | None:
    """Plus grand = préférable : plus gros modèle de chat actif ; exclut audio, sécurité, embeddings."""
    m = mid.lower()
    if any(x in m for x in ("whisper", "guard", "tts", "embed", "orpheus", "playai", "moderation", "safeguard", "image", "distil")):
        return None
    if (meta or {}).get("active") is False:
        return None
    sizes = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)b", m)]
    return (max(sizes) if sizes else 0.0) + (5 if any(k in m for k in ("versatile", "instruct", "chat")) else 0)


class OpenAICompatClient:
    def __init__(self, pool: KeyPool, base_url: str, model: str, session=None, timeout: int = 120):
        self.pool, self.base, self.timeout = pool, base_url.rstrip("/"), timeout
        self.models = [model]
        self._bad: set[str] = set()
        self._discovered = False
        self._json_mode = True
        self.http = session or requests.Session()

    @property
    def model(self) -> str:
        for m in self.models:
            if m not in self._bad:
                return m
        raise LLMError("aucun modèle de secours utilisable : définis FALLBACK_LLM_MODEL")

    def _discover(self, key: str) -> None:
        self._discovered = True
        try:
            r = self.http.get(f"{self.base}/models", headers={"Authorization": f"Bearer {key}"}, timeout=30)
            items = r.json().get("data", [])
        except (requests.RequestException, ValueError, AttributeError):
            return
        free_only = "openrouter" in self.base
        ranked = sorted(((_open_rank(i.get("id", ""), i), i.get("id", "")) for i in items
                         if i.get("id") and (not free_only or i["id"].endswith(":free"))),
                        key=lambda x: (-(x[0] if x[0] is not None else -1), x[1]))
        names = [n for sc, n in ranked if sc is not None]
        for n in names[:4]:
            if n not in self.models:
                self.models.append(n)
        log.info("Modèles de secours découverts : %s", names[:4])

    def generate_json(self, prompt: str, video_url=None, temperature: float = 0.1, until: float | None = None):
        if video_url:
            raise LLMError("repli texte uniquement")
        last = ""
        for attempt in range(max(6, 2 * len(self.pool._next_ok)) + 6):
            lim = min(x for x in (getattr(self, "deadline", None), until, float("inf")) if x)
            if time.time() > lim:
                raise QuotaExhausted("temps imparti écoulé")
            key = self.pool.acquire()
            if key is None:
                raise QuotaExhausted(f"clés de repli refusées ou épuisées (dernière erreur : {last or 'aucune'})")
            body = {"model": self.model, "temperature": temperature,
                    "messages": [{"role": "system", "content": "Réponds uniquement par un objet JSON valide."},
                                 {"role": "user", "content": prompt}]}
            if self._json_mode:
                body["response_format"] = {"type": "json_object"}
            try:
                r = self.http.post(f"{self.base}/chat/completions", json=body, timeout=max(10.0, min(self.timeout, lim - time.time())),
                                   headers={"Authorization": f"Bearer {key}"})
            except requests.RequestException as e:
                last = str(e)
                time.sleep(2)
                continue
            if r.status_code == 200:
                try:
                    return parse_json_loose(r.json()["choices"][0]["message"]["content"])
                except (KeyError, IndexError, ValueError, TypeError) as e:
                    last = f"réponse illisible: {e}"
                    continue
            last = f"HTTP {r.status_code}: {r.text[:300]}"
            low = r.text.lower()
            if r.status_code in (400, 404) and "model" in low and re.search(r"does not exist|decommission|not found|no access|deprecated|not supported", low):
                log.warning("Modèle de secours %s refusé, on change de modèle", self.model)
                self._bad.add(self.model)
                if not self._discovered:
                    self._discover(key)
                continue
            if r.status_code == 400 and self._json_mode and ("response_format" in low or "json" in low):
                self._json_mode = False  # certains modèles n'ont pas le mode JSON : le prompt suffit
                continue
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
                           DEFAULT_MODELS, preferred=[forced] if forced else []) if g else None
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

    deadline: float | None = None
    MEDIA_CALL_MAX = 300.0  # secondes max pour analyser UNE vidéo/un audio (toutes tentatives comprises)
    TEXT_CALL_MAX = 150.0

    def set_deadline(self, t: float | None) -> None:
        self.deadline = t
        for c in (self.gemini, self.fallback):
            if c:
                c.deadline = t

    @property
    def has_text(self) -> bool:
        return bool(self.gemini or self.fallback)

    @property
    def has_video(self) -> bool:
        return bool(self.gemini and self.gemini.pool.available())

    def generate_json(self, prompt: str, video_url: str | None = None, inline_media=None, media_key: str = ""):
        if self.deadline and time.time() > self.deadline:
            raise QuotaExhausted("temps imparti écoulé")
        ck = f"{video_url or ''}|{media_key}\n{prompt}"
        hit = cache.get("llm", ck)
        if hit is not None:
            return hit
        err = None
        media = bool(video_url or inline_media)
        # plafond par appel : une vidéo ou un quota capricieux ne doit jamais bloquer toute la recherche
        until = time.time() + (self.MEDIA_CALL_MAX if media else self.TEXT_CALL_MAX)
        if self.gemini and self.gemini.pool.available():
            try:
                out = self.gemini.generate_json(prompt, video_url, inline_media=inline_media, until=until,
                                                max_attempts=(4 if self.fallback and not media else (6 if media else None)))
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
            out = self.fallback.generate_json(prompt, until=time.time() + self.TEXT_CALL_MAX)
            cache.put("llm", ck, out)
            return out
        raise err or QuotaExhausted("aucun LLM disponible")
