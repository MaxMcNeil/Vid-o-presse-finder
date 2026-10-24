from __future__ import annotations

import json
import re
import unicodedata


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def fmt_ts(seconds: float | None) -> str:
    if seconds is None:
        return ""
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def parse_ts(value) -> float | None:
    """Accepte 123, '123', '12:03', '1:02:03'."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    parts = str(value).strip().split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return None
    t = 0.0
    for n in nums:
        t = t * 60 + n
    return t


def parse_json_loose(text: str):
    """Extrait du JSON d'une réponse de LLM (balises ``` tolérées)."""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"(\{.*\}|\[.*\])", text, flags=re.S)
        if m:
            return json.loads(m.group(1))
        raise


def parse_iso_duration(d: str) -> int | None:
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", d or "")
    if not m:
        return None
    dd, h, mi, s = (int(x or 0) for x in m.groups())
    return dd * 86400 + h * 3600 + mi * 60 + s


def slugify(s: str, n: int = 60) -> str:
    return re.sub(r"\s+", "-", normalize(s))[:n].strip("-") or "recherche"


def call_with_timeout(fn, timeout: float, *a, **k):
    """Exécute fn dans un fil séparé ; TimeoutError si trop long (le fil est abandonné, jamais attendu)."""
    import threading
    box: dict = {}

    def run():
        try:
            box["v"] = fn(*a, **k)
        except BaseException as e:  # noqa: BLE001
            box["e"] = e
    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(timeout)
    if th.is_alive():
        raise TimeoutError(f"plus de {timeout:.0f} s")
    if "e" in box:
        raise box["e"]
    return box.get("v")
