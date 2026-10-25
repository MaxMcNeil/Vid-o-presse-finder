"""Pool de clés API : rotation, limitation de débit par clé, mise en quarantaine."""
from __future__ import annotations

import os
import re
import threading
import time


def parse_keys(raw: str | None) -> list[str]:
    return [k for k in re.split(r"[\s,;]+", raw or "") if k]


def keys_from_env(prefix: str) -> list[str]:
    """Lit PREFIX_KEYS, PREFIX_KEYS_PLUS, PREFIX_KEY, PREFIX_KEY_1..20 et les mêmes avec PREFIX_API_ (ex. GEMINI_API_KEYS,
    GEMINI_API_KEYS_PLUS). *_PLUS = clés ajoutées en plus, sans écraser le secret existant (illisible sur GitHub)."""
    out: list[str] = []
    for base in (prefix, f"{prefix}_API"):
        out += parse_keys(os.environ.get(f"{base}_KEYS"))
        out += parse_keys(os.environ.get(f"{base}_KEYS_PLUS"))  # clés ajoutées EN PLUS (sans toucher aux anciennes)
        out += parse_keys(os.environ.get(f"{base}_KEY"))
        for i in range(1, 21):
            out += parse_keys(os.environ.get(f"{base}_KEY_{i}"))
    seen, res = set(), []
    for k in out:
        if k not in seen:
            seen.add(k)
            res.append(k)
    return res


LABELS = {"gemini": "Gemini", "youtube": "YouTube", "guardian": "Guardian", "nyt": "NYT", "groq": "Groq",
          "cerebras": "Cerebras", "openrouter": "OpenRouter", "fallback": "LLM de secours"}


class KeyPool:
    """Rotation des clés d'UNE api. Avec persist=True, l'état est lu/écrit dans quota.STATE : une clé épuisée lors
    d'un lancement précédent est sautée d'emblée jusqu'à la remise à zéro (minuit, heure du Pacifique).
    QF_KEY_OFFSET (numéro du job en parallèle) fait commencer chaque job par une clé différente."""

    def __init__(self, name: str, keys: list[str], min_interval: float = 0.0, persist: bool = False):
        self.name = name
        self.min_interval = min_interval
        self.persist = persist
        self._lock = threading.Lock()
        self.labels = {k: f"{LABELS.get(name, name)} clé {i}" for i, k in enumerate(keys, 1)}
        off = 0
        if persist and keys:
            try:
                off = int(os.environ.get("QF_KEY_OFFSET", "0")) % len(keys)
            except ValueError:
                off = 0
        order = keys[off:] + keys[:off]
        self._next_ok = {k: 0.0 for k in order}
        self._dead_until = {k: 0.0 for k in order}
        if persist:
            from . import quota
            st = quota.state()
            for k in order:
                st.label(name, k, self.labels[k])
                self._dead_until[k] = st.key_until(name, k)
            n = sum(1 for k in order if self._dead_until[k])
            if n:
                import logging
                logging.getLogger("qf.keys").info("%s : %d/%d clé(s) déjà épuisée(s) aujourd'hui (lancements précédents), sautée(s)",
                                                  LABELS.get(name, name), n, len(order))

    def label(self, key: str) -> str:
        return self.labels.get(key, "clé ?")

    def __bool__(self):
        return bool(self._next_ok)

    def available(self) -> int:
        now = time.time()
        with self._lock:
            return sum(1 for k in self._next_ok if self._dead_until[k] <= now)

    def acquire(self, wait: bool = True) -> str | None:
        """Renvoie la clé utilisable le plus tôt (en attendant si nécessaire)."""
        while True:
            with self._lock:
                now = time.time()
                alive = [k for k in self._next_ok if self._dead_until[k] <= now]
                if not alive:
                    soonest = min(self._dead_until.values(), default=0) - now
                    if not wait or soonest > 180:  # vraiment épuisé (quota du jour) : on abandonne cette api
                        return None
                    nap = max(0.2, soonest)
                else:
                    nap = 0.0
            if nap:
                time.sleep(nap)  # simple pause (limite par minute) : on attend la clé qui revient le plus tôt
                continue
            with self._lock:
                now = time.time()
                alive = [k for k in self._next_ok if self._dead_until[k] <= now]
                if not alive:
                    continue
                key = min(alive, key=lambda k: self._next_ok[k])
                delay = self._next_ok[key] - now
                if delay <= 0 or not wait:
                    if delay > 0:
                        return None
                    self._next_ok[key] = now + self.min_interval
                    return key
                # on réserve le créneau puis on dort hors verrou
                self._next_ok[key] = self._next_ok[key] + self.min_interval
            time.sleep(delay)
            return key

    def cooldown(self, key: str, seconds: float):
        with self._lock:
            if key in self._dead_until:
                self._dead_until[key] = time.time() + seconds

    def exhaust(self, key: str, why: str = "quota du jour épuisé"):
        """Quota journalier épuisé (ou clé invalide) : hors service jusqu'à la remise à zéro, mémorisé pour les prochains lancements."""
        from . import quota
        until = quota.next_reset()
        with self._lock:
            if key in self._dead_until:
                self._dead_until[key] = until
        if self.persist:
            quota.state().exhaust_key(self.name, key, until, why)

    def use(self, key: str, units: int = 1, model: str = "") -> None:
        if self.persist:
            from . import quota
            quota.state().use(self.name, key, units, model)

    def note_error(self, key: str, msg: str) -> None:
        if self.persist:
            from . import quota
            quota.state().error(self.name, key, msg)

    def status(self) -> str:
        now = time.time()
        ok = sum(1 for k in self._next_ok if self._dead_until[k] <= now)
        return f"{ok}/{len(self._next_ok)} disponible(s)"
