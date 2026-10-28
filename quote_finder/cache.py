from __future__ import annotations

import hashlib
import json
import threading
import os
from pathlib import Path

ROOT = Path(os.environ.get("QF_CACHE_DIR", ".cache"))


def _path(ns: str, key: str) -> Path:
    h = hashlib.sha256(key.encode()).hexdigest()[:32]
    return ROOT / ns / f"{h}.json"


def get(ns: str, key: str):
    p = _path(ns, key)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def put(ns: str, key: str, value) -> None:
    p = _path(ns, key)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass
