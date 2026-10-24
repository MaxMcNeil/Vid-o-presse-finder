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


def is_text_only(name: str) -> bool:
    return name.startswith("gemma")


def _model_rank(name: str):
    """Plus grand = préférable : version la plus récente, famille 'flash', puis 'flash-lite', puis Gemma (texte seul).
    Chaque modèle a son propre quota gratuit : plus il y en a dans la liste, plus on peut travailler."""
    n = name.replace("models/", "")
    if n.startswith("gemma-"):  # quota gratuit très large (plusieurs milliers de requêtes/jour), texte uniquement
        sizes = [float(x) for x in re.findall(r"(\d+)b", n)]
        if not sizes or max(sizes) < 12:
            return None
        return -10 + max(sizes) / 100
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
        self._nojson: set[str] = set()                   # modèles sans mode JSON (Gemma)
        self.last_model = (self.preferred or self.models or ["?"])[0]
        self.used: dict[str, int] = {}
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
        log.info("%s : %d modèle(s) accessible(s) : %s", self.pool.label(key), len(lst), lst[:6])
        return lst

    def _pick(self, key: str, media: bool = False) -> tuple[str | None, float]:
        """Meilleur modèle disponible pour cette clé ; sinon (None, secondes avant le prochain retour possible)."""
        from . import quota
        now = time.time()
        soon = float("inf")
        st = quota.state() if self.pool.persist else None
        for m in self._models_for(key):
            if (key, m) in self._bad or (media and is_text_only(m)):
                continue
            free_at = max(self._until.get((key, m), 0), self._cool.get(m, 0),
                          st.model_until("gemini", key, m) if st else 0)
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
            m, soon = self._pick(key, media)
            if m is None:
                if soon <= 150:  # pause courte (surcharge, limite par minute) : on patiente
                    if self.pool.available() > 1:
                        self.pool.cooldown(key, soon)  # une autre clé est peut-être libre tout de suite
                    else:
                        time.sleep(min(soon + 0.5, 20))
                elif soon > 3600 and not media:  # tous les modèles de cette clé sont épuisés pour la journée
                    log.warning("Gemini : %s n'a plus aucun modèle disponible aujourd'hui → clé suivante", self.pool.label(key))
                    self.pool.exhaust(key, "tous les modèles épuisés")
                else:
                    self.pool.cooldown(key, soon)
                continue
            self.last_model = m
            body = {"contents": [{"role": "user", "parts": parts}], "generationConfig": {"temperature": temperature}}
            if m not in self._nojson:
                body["generationConfig"]["responseMimeType"] = "application/json"
            think = self._think.get(m, 0)
            if is_text_only(m):
                pass  # Gemma : pas de réglage de réflexion
            elif think == 0:
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
                    self.pool.use(key, model=m)
                    self.used[m] = self.used.get(m, 0) + 1
                    return out
                except (KeyError, IndexError, ValueError, TypeError) as e:
                    last = f"réponse illisible: {e}"
                    continue
            msg = r.text[:400]
            last = f"HTTP {r.status_code} ({m}): {msg}"
            low = msg.lower()
            self.pool.note_error(key, f"{m}: HTTP {r.status_code} {msg[:100]}")
            if r.status_code == 400 and ("json mode" in low or "response_mime_type" in low or "responsemimetype" in low):
                self._nojson.add(m)
                continue
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
                    from . import quota
                    log.warning("Gemini : quota du jour de %s atteint (%s) → autre modèle ou autre clé", m, self.pool.label(key))
                    self._until[(key, m)] = quota.next_reset()
                    if self.pool.persist:
                        quota.state().exhaust_model("gemini", key, m)
                else:
                    d = re.search(r"retry in ([\d.]+)s", msg) or re.search(r'"retryDelay":\s*"(\d+)s"', msg)
                    self._until[(key, m)] = time.time() + (float(d.group(1)) + 1 if d else 30)
                continue
            if r.status_code in (401, 403):
                if "api key" in low or "api_key" in low:
                    self.pool.exhaust(key, "clé refusée (invalide ou révoquée ?)")
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
        self.name = "relais"
        self.calls = 0
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
                raise QuotaExhausted(f"clés {self.name} refusées ou épuisées (dernière erreur : {last or 'aucune'})")
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
                    out = parse_json_loose(r.json()["choices"][0]["message"]["content"])
                    self.calls += 1
                    self.pool.use(key, model=self.model)
                    return out
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
            self.pool.note_error(key, f"HTTP {r.status_code} {r.text[:100]}")
            if r.status_code == 429:
                if re.search(r"per day|daily|tokens per day|requests per day|\brpd\b|\btpd\b|credits", low):
                    log.warning("%s : quota du jour atteint (%s) → clé suivante", self.name, self.pool.label(key))
                    self.pool.exhaust(key)
                else:
                    ra = r.headers.get("retry-after") if hasattr(r, "headers") and r.headers else None
                    try:
                        wait = float(ra) if ra else 60.0
                    except ValueError:
                        wait = 60.0
                    self.pool.cooldown(key, min(max(wait, 2.0), 600.0))
                continue
            if r.status_code in (401, 403):
                self.pool.exhaust(key, "clé refusée (invalide ?)")
                continue
            if r.status_code >= 500:
                time.sleep(3)
                continue
            raise LLMError(last)
        raise LLMError(last)


def guess_provider(key: str) -> str:
    k = key.strip()
    if k.startswith("gsk_"):
        return "groq"
    if k.startswith("csk-"):
        return "cerebras"
    if k.startswith("sk-or-"):
        return "openrouter"
    return ""


class LLMRouter:
    """Essaie Gemini, puis le repli (texte seulement). Met les réponses en cache disque."""

    def __init__(self, gemini: GeminiClient | None, fallback=None, fallbacks: list | None = None):
        self.gemini = gemini
        self.fallbacks = list(fallbacks or ([fallback] if fallback else []))

    @property
    def fallback(self):
        return self.fallbacks[0] if self.fallbacks else None

    @classmethod
    def from_env(cls) -> "LLMRouter":
        """Ordre : Gemini (5 clés × tous les modèles flash, flash-lite puis Gemma) → Groq → Cerebras → OpenRouter.
        Chaque relais a ses propres clés : GROQ_API_KEYS, CEREBRAS_API_KEYS, OPENROUTER_API_KEYS
        (ou l'ancien couple FALLBACK_LLM_KEYS + FALLBACK_LLM_PROVIDER)."""
        g = keys_from_env("GEMINI")
        forced = os.environ.get("GEMINI_MODEL")
        gem = GeminiClient(KeyPool("gemini", g, min_interval=float(os.environ.get("GEMINI_MIN_INTERVAL", "4")), persist=True),
                           DEFAULT_MODELS, preferred=[forced] if forced else []) if g else None
        fbs = []
        legacy = os.environ.get("FALLBACK_LLM_PROVIDER", "").lower().strip()
        # clés de FALLBACK_LLM_KEYS : le service est reconnu à leur début (gsk_ = Groq, csk- = Cerebras, sk-or- = OpenRouter)
        by_prov: dict[str, list[str]] = {}
        for k in keys_from_env("FALLBACK_LLM"):
            prov = guess_provider(k) or (legacy if legacy in PROVIDERS else "")
            if prov:
                by_prov.setdefault(prov, []).append(k)
        for prov in ("groq", "cerebras", "openrouter"):
            keys = list(dict.fromkeys(keys_from_env(prov.upper()) + by_prov.get(prov, [])))
            if not keys:
                continue
            base, model = PROVIDERS[prov]
            if prov == legacy:
                base = os.environ.get("FALLBACK_LLM_BASE_URL") or base
                model = os.environ.get("FALLBACK_LLM_MODEL") or model
            c = OpenAICompatClient(KeyPool(prov, keys, min_interval=2.5, persist=True), base, model)
            c.name = prov
            fbs.append(c)
        if legacy and legacy not in PROVIDERS and keys_from_env("FALLBACK_LLM") and os.environ.get("FALLBACK_LLM_BASE_URL"):
            c = OpenAICompatClient(KeyPool("fallback", keys_from_env("FALLBACK_LLM"), 2.5, persist=True),
                                   os.environ["FALLBACK_LLM_BASE_URL"], os.environ.get("FALLBACK_LLM_MODEL", ""))
            c.name = legacy
            fbs.append(c)
        return cls(gem, fallbacks=fbs)

    deadline: float | None = None
    MEDIA_CALL_MAX = 300.0  # secondes max pour analyser UNE vidéo/un audio (toutes tentatives comprises)
    TEXT_CALL_MAX = 150.0

    def set_deadline(self, t: float | None) -> None:
        self.deadline = t
        for c in [self.gemini] + self.fallbacks:
            if c:
                c.deadline = t

    @property
    def has_text(self) -> bool:
        return bool(self.gemini or self.fallbacks)

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
                                                max_attempts=(None if not media else 6))
                cache.put("llm", ck, out)
                return out
            except QuotaExhausted as e:
                err = e
                if not media and self.fallbacks:
                    log.warning("Gemini indisponible (%s) → relais %s", str(e)[:100], self.fallbacks[0].name)
            except LLMError as e:
                if media:
                    raise
                err = e
                log.warning("Gemini a échoué (%s), tentative de relais", str(e)[:120])
        if not media:
            for fb in self.fallbacks:  # Groq, puis Cerebras, puis OpenRouter : chacun avec son propre quota
                if not fb.pool.available():
                    continue
                try:
                    out = fb.generate_json(prompt, until=time.time() + self.TEXT_CALL_MAX)
                    cache.put("llm", ck, out)
                    return out
                except LLMError as e:
                    err = e
                    log.warning("Relais %s indisponible (%s)", getattr(fb, "name", "?"), str(e)[:100])
        raise err or QuotaExhausted("aucun LLM disponible")

    def usage(self) -> dict:
        """Résumé pour le rapport : état des clés et modèles réellement utilisés."""
        d = {}
        if self.gemini:
            d["gemini"] = {"clés": self.gemini.pool.status(), "modèles utilisés": dict(self.gemini.used)}
        for fb in self.fallbacks:
            d[getattr(fb, "name", "relais")] = {"clés": fb.pool.status(), "appels": getattr(fb, "calls", 0)}
        return d
