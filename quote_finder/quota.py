"""Mémoire des quotas entre les lancements : quelles clés sont épuisées, jusqu'à quand, combien on a consommé.

Les quotas gratuits Google (Gemini, YouTube) repartent à zéro à minuit, heure du Pacifique.
Le fichier (state/quota.json) est enregistré dans le dépôt par le workflow : chaque lancement saute directement
les clés déjà épuisées au lieu de perdre du temps (et des tentatives) dessus.
Les clés n'y apparaissent jamais : seulement une empreinte (sha256 tronqué) et leur numéro dans le secret.

    python -m quote_finder.quota merge state/quota.json autre1.json autre2.json   -> fusion + state/QUOTAS.md
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys
import threading
import time
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    PACIFIC = ZoneInfo("America/Los_Angeles")
except Exception:  # noqa: BLE001
    PACIFIC = dt.timezone(dt.timedelta(hours=-8))

YT_DAILY_UNITS = 10_000          # quota gratuit par clé YouTube (projet Google Cloud)
YT_COST = {"search": 100, "videos": 1, "channels": 1}
_lock = threading.Lock()


def fp(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:10]


def pacific_day(t: float | None = None) -> str:
    return dt.datetime.fromtimestamp(t or time.time(), PACIFIC).strftime("%Y-%m-%d")


def next_reset(t: float | None = None) -> float:
    """Prochain minuit heure du Pacifique (remise à zéro des quotas Google)."""
    now = dt.datetime.fromtimestamp(t or time.time(), PACIFIC)
    nxt = (now + dt.timedelta(days=1)).replace(hour=0, minute=1, second=0, microsecond=0)
    return nxt.timestamp()


class QuotaState:
    """{"keys": {"gemini:<fp>": {"label": "Gemini clé 2", "until": t, "models": {"gemini-x": t}, "day": "...",
                                   "calls": n, "units": n, "errors": n, "last": "..."}}}"""

    def __init__(self, path: str | os.PathLike | None = None):
        self.path = Path(path or os.environ.get("QF_QUOTA_FILE", "state/quota.json"))
        self.data: dict = {"keys": {}}
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
            self.data.setdefault("keys", {})
        except (OSError, ValueError):
            pass
        self._purge()

    def _purge(self) -> None:
        now, today = time.time(), pacific_day()
        for k, e in self.data["keys"].items():
            if e.get("until", 0) < now:
                e.pop("until", None)
            e["models"] = {m: u for m, u in (e.get("models") or {}).items() if u > now}
            if e.get("day") != today:  # nouvelle journée Pacifique : compteurs à zéro
                e.update(day=today, calls=0, units=0, errors=0)

    def _e(self, api: str, key: str, label: str = "") -> dict:
        e = self.data["keys"].setdefault(f"{api}:{fp(key)}", {"day": pacific_day(), "calls": 0, "units": 0, "errors": 0,
                                                              "models": {}})
        if label:
            e["label"] = label
        return e

    # ---- lecture
    def key_until(self, api: str, key: str) -> float:
        e = self.data["keys"].get(f"{api}:{fp(key)}") or {}
        u = e.get("until", 0)
        if api == "youtube" and e.get("day") == pacific_day() and e.get("units", 0) >= YT_DAILY_UNITS - 150:
            u = max(u, next_reset())  # notre propre compteur dit que la clé est vide
        return u if u > time.time() else 0.0

    def model_until(self, api: str, key: str, model: str) -> float:
        u = ((self.data["keys"].get(f"{api}:{fp(key)}") or {}).get("models") or {}).get(model, 0)
        return u if u > time.time() else 0.0

    def units_left(self, api: str, key: str) -> int:
        e = self.data["keys"].get(f"{api}:{fp(key)}") or {}
        used = e.get("units", 0) if e.get("day") == pacific_day() else 0
        return max(0, YT_DAILY_UNITS - used)

    # ---- écriture
    def label(self, api: str, key: str, label: str) -> None:
        with _lock:
            self._e(api, key, label)

    def use(self, api: str, key: str, units: int = 1, model: str = "") -> None:
        with _lock:
            e = self._e(api, key)
            e["calls"] = e.get("calls", 0) + 1
            e["units"] = e.get("units", 0) + units
            if model:
                e.setdefault("used_models", {})
                e["used_models"][model] = e["used_models"].get(model, 0) + 1

    def error(self, api: str, key: str, msg: str) -> None:
        with _lock:
            e = self._e(api, key)
            e["errors"] = e.get("errors", 0) + 1
            e["last"] = msg[:160]

    def exhaust_key(self, api: str, key: str, until: float | None = None, why: str = "quota du jour épuisé") -> None:
        with _lock:
            e = self._e(api, key)
            e["until"] = max(e.get("until", 0), until or next_reset())
            e["last"] = why

    def exhaust_model(self, api: str, key: str, model: str, until: float | None = None) -> None:
        with _lock:
            e = self._e(api, key)
            e.setdefault("models", {})[model] = max(e["models"].get(model, 0), until or next_reset())

    def save(self) -> None:
        with _lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.data["saved"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
                self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
            except OSError:
                pass

    # ---- synthèse
    def summary(self) -> list[str]:
        now = time.time()
        rows = []
        for k, e in sorted(self.data["keys"].items(), key=lambda x: (x[0].split(":")[0], x[1].get("label", ""))):
            api = k.split(":")[0]
            state = "✔ disponible"
            if e.get("until", 0) > now:
                state = f"✘ épuisée jusqu'à {dt.datetime.fromtimestamp(e['until'], dt.timezone.utc).strftime('%d/%m %H:%M')} UTC"
            elif e.get("models"):
                state = f"⚠ {len(e['models'])} modèle(s) épuisé(s), les autres restent utilisables"
            extra = f"{e.get('units', 0)} unités / {YT_DAILY_UNITS}" if api == "youtube" else f"{e.get('calls', 0)} appels"
            if e.get("used_models"):
                extra += " — " + ", ".join(f"{m} ×{n}" for m, n in sorted(e["used_models"].items(), key=lambda x: -x[1])[:3])
            last = " ".join(str(e.get("last", "")).split()).replace("|", "/")[:90]  # tableau Markdown sur une ligne
            rows.append(f"| {e.get('label', k)} | {state} | {extra} aujourd'hui | {e.get('errors', 0)} | {last} |")
        return rows

    def markdown(self) -> str:
        L = ["# État des clés API", "",
             f"Mis à jour : {dt.datetime.now(dt.timezone.utc).strftime('%d/%m/%Y %H:%M')} UTC. "
             "Les quotas gratuits Google repartent à zéro à **minuit heure du Pacifique (9 h à Paris/Rome)**.", "",
             "| Clé | État | Consommation | Erreurs | Dernier message |", "|---|---|---|---|---|"]
        return "\n".join(L + self.summary()) + "\n"


_STATE: QuotaState | None = None


def state() -> QuotaState:
    """État partagé par tout le processus (chargé une fois)."""
    global _STATE
    with _lock:
        if _STATE is None:
            _STATE = QuotaState()
    return _STATE


def reset_state(path=None) -> QuotaState:
    global _STATE
    _STATE = QuotaState(path)
    return _STATE


def merge(target: Path, others: list[Path]) -> QuotaState:
    """Fusionne les états des recherches lancées en parallèle. Chaque job est parti du même fichier (target) :
    on ajoute la consommation propre à chaque job et on garde les épuisements les plus pessimistes."""
    base = QuotaState(target)
    orig = json.loads(json.dumps(base.data["keys"]))  # état de départ commun à tous les jobs
    counters = ("calls", "units", "errors")
    for p in others:
        try:
            d = json.loads(Path(p).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for k, e in (d.get("keys") or {}).items():
            b = base.data["keys"].setdefault(k, {"models": {}, "day": e.get("day", ""), "calls": 0, "units": 0, "errors": 0})
            o = orig.get(k) or {}
            for f in ("label", "last"):
                if e.get(f):
                    b[f] = e[f]
            b["until"] = max(b.get("until", 0), e.get("until", 0))
            for m, u in (e.get("models") or {}).items():
                b.setdefault("models", {})[m] = max(b["models"].get(m, 0), u)
            if e.get("day", "") > b.get("day", ""):  # le job a vu une nouvelle journée Pacifique
                b.update(day=e["day"], calls=0, units=0, errors=0, used_models={})
            if e.get("day") == b.get("day"):
                same = o.get("day") == e.get("day")
                for f in counters:
                    b[f] = b.get(f, 0) + max(0, e.get(f, 0) - (o.get(f, 0) if same else 0))
                om = (o.get("used_models") or {}) if same else {}
                for m, n in (e.get("used_models") or {}).items():
                    b.setdefault("used_models", {})[m] = b["used_models"].get(m, 0) + max(0, n - om.get(m, 0))
    base._purge()
    return base


def main(argv=None) -> int:
    a = list(sys.argv[1:] if argv is None else argv)
    if len(a) >= 2 and a[0] == "merge":
        target = Path(a[1])
        st = merge(target, [Path(p) for p in a[2:]])
        st.path = target
        st.save()
        (target.parent / "QUOTAS.md").write_text(st.markdown(), encoding="utf-8")
        print(st.markdown())
        return 0
    if a and a[0] == "show":
        print(QuotaState(a[1] if len(a) > 1 else None).markdown())
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
