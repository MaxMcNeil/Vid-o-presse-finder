from __future__ import annotations

import json
import re
import unicodedata


# arabe : variantes d'écriture ramenées à une forme (ى→ي, ة→ه, tatweel supprimé), chiffres arabes-indiens → 0-9
_AR = str.maketrans({"ى": "ي", "ة": "ه", "ـ": "", "ٱ": "ا", **{chr(0x0660 + i): str(i) for i in range(10)},
                     **{chr(0x06F0 + i): str(i) for i in range(10)}})


def normalize(s: str) -> str:
    """Minuscules, sans accents ni ponctuation. Garde TOUTES les écritures (latin, arabe, cyrillique...) :
    l'ancienne version effaçait l'arabe, ce qui rendait toute recherche en arabe impossible."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))  # accents latins et voyelles brèves arabes (tashkil)
    s = s.lower().translate(_AR)
    s = re.sub(r"[\W_]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def has_arabic(s: str) -> bool:
    return bool(re.search(r"[\u0600-\u06FF]", s or ""))


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
    """Nom de dossier/fichier en ASCII (sûr pour GitHub, artefacts, téléphones). Texte non latin (arabe...) :
    la partie latine éventuelle + une empreinte courte, stable pour un même texte."""
    import hashlib
    words = normalize(s).split()
    latin = [w for w in words if re.fullmatch(r"[a-z0-9]+", w)]
    slug = "-".join(latin)[:n].strip("-")
    if len(latin) < len(words):  # une partie du texte n'est pas en écriture latine
        h = hashlib.sha1((s or "").encode()).hexdigest()[:6]
        slug = f"{slug[: max(0, n - 7)].strip('-')}-{h}".strip("-")
    return slug or "recherche"


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


def items_of(d, key: str) -> list:
    """Liste attendue sous d[key], quelle que soit la forme renvoyée par l'IA :
    {"hits": [...]}, [...] directement, [{"hits": [...]}], un seul élément {...}, ou rien."""
    if d is None:
        return []
    if isinstance(d, list):
        if len(d) == 1 and isinstance(d[0], dict) and key in d[0]:
            return items_of(d[0], key)
        return [x for x in d if isinstance(x, dict)]
    if isinstance(d, dict):
        v = d.get(key)
        if isinstance(v, list):
            return [x for x in v if isinstance(x, dict)]
        if isinstance(v, dict):
            return [v]
        if v is None and d and key not in d and any(k in d for k in ("t", "quote", "i", "s")):
            return [d]  # un seul élément renvoyé sans enveloppe
    return []


def as_dict(d) -> dict:
    """Objet attendu : accepte aussi [ {...} ]."""
    if isinstance(d, dict):
        return d
    if isinstance(d, list) and d and isinstance(d[0], dict):
        return d[0]
    raise ValueError(f"réponse IA inattendue : {type(d).__name__}")
