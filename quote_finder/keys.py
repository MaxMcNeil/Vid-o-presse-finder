"""Pool de clés API : rotation, limitation de débit par clé, mise en quarantaine."""
from __future__ import annotations

import os
import re
import threading
import time


def parse_keys(raw: str | None) -> list[str]:
    return [k for k in re.split(r"[\s,;]+", raw or "") if k]


def keys_from_env(prefix: str) -> list[str]:
    """PREFIX_KEYS="a,b,c" + PREFIX_KEY + PREFIX_KEY_1..PREFIX_KEY_20."""
    out = parse_keys(os.environ.get(f"{prefix}_KEYS"))
    out += parse_keys(os.environ.get(f"{prefix}_KEY"))
    for i in range(1, 21):
        out += parse_keys(os.environ.get(f"{prefix}_KEY_{i}"))
    seen, res = set(), []
    for k in out:
        if k not in seen:
            seen.add(k)
            res.append(k)
    return res


class KeyPool:
    def __init__(self, name: str, keys: list[str], min_interval: float = 0.0):
        self.name = name
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._next_ok = {k: 0.0 for k in keys}
        self._dead_until = {k: 0.0 for k in keys}

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
                    return None
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

    def exhaust(self, key: str):
        """Quota journalier épuisé : hors service pour le reste du run."""
        self.cooldown(key, 24 * 3600)
