"""Déclenchement du téléphone (Termux) depuis le workflow GitHub, via ntfy.sh.

- Workflow  : signe la demande (HMAC) et la publie sur un sujet ntfy secret, puis attend l'accusé de réception.
- Téléphone : `python -m quote_finder.phone` écoute le sujet, vérifie la signature, lance la recherche, pousse les résultats.
Ce module n'utilise que la bibliothèque standard.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

NTFY = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
MAX_AGE = 600  # une demande plus vieille que 10 min est ignorée (GitHub a déjà pris le relais)


# ---------------------------------------------------------------- signature / validation
def _canon(d: dict) -> bytes:
    return json.dumps({k: v for k, v in d.items() if k != "sig"}, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode()


def sign(d: dict, secret: str) -> str:
    return hmac.new(secret.encode(), _canon(d), hashlib.sha256).hexdigest()


def verify(d: dict, secret: str, now: float | None = None) -> bool:
    sig = d.get("sig", "")
    if not isinstance(sig, str) or not hmac.compare_digest(sig, sign(d, secret)):
        return False
    ts = d.get("ts")
    return isinstance(ts, (int, float)) and abs((now or time.time()) - ts) <= MAX_AGE


def build_request(person: str, claim: str, also: str, anyone: bool, strict: bool, max_videos: int, langs: str,
                  secret: str) -> dict:
    d = {"id": secrets.token_hex(6), "ts": int(time.time()), "person": person, "claim": claim, "also": also,
         "anyone": bool(anyone), "strict": bool(strict), "max_videos": int(max_videos), "langs": langs}
    d["sig"] = sign(d, secret)
    return d


def to_args(d: dict) -> list[str] | None:
    """Valide une demande et la convertit en arguments (forme --opt=valeur : pas d'injection d'option, jamais de shell)."""
    try:
        person, claim, also, langs = d["person"], d["claim"], d.get("also", ""), d.get("langs", "en,fr")
        if not all(isinstance(x, str) for x in (person, claim, also, langs)):
            return None
        if not (1 <= len(person) <= 200 and 1 <= len(claim) <= 600 and len(also) <= 300):
            return None
        if not re.fullmatch(r"[a-z]{2}(,[a-z]{2}){0,5}", langs):
            return None
        mv = int(d.get("max_videos", 30))
        if not 1 <= mv <= 100:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    a = [f"--person={person}", f"--claim={claim}", f"--also={also}", f"--langs={langs}", f"--max-videos={mv}"]
    if d.get("anyone") is True:
        a.append("--anyone")
    if d.get("strict") is True:
        a.append("--strict")
    return a


# ---------------------------------------------------------------- ntfy (stdlib)
def publish(topic: str, text: str, server: str = NTFY) -> None:
    req = urllib.request.Request(f"{server}/{topic}", data=text.encode(), method="POST",
                                 headers={"Content-Type": "text/plain; charset=utf-8"})
    urllib.request.urlopen(req, timeout=20).read()


def poll(topic: str, since: str = "10m", server: str = NTFY) -> list[str]:
    with urllib.request.urlopen(f"{server}/{topic}/json?poll=1&since={since}", timeout=20) as r:
        out = []
        for line in r.read().decode().splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("event") == "message":
                out.append(e.get("message", ""))
        return out


# ---------------------------------------------------------------- côté workflow
def route(env=os.environ, publish_fn=publish, poll_fn=poll, wait: int = 100, sleep=time.sleep) -> bool:
    """Renvoie True si GitHub doit faire la recherche lui-même."""
    where = env.get("WHERE", "phone-then-github")
    topic, secret = env.get("NTFY_TOPIC", ""), env.get("PHONE_SECRET", "")
    if where == "github":
        print("Mode GitHub demandé.")
        return True
    if not topic or not secret:
        print("NTFY_TOPIC / PHONE_SECRET absents : recherche sur GitHub.")
        return True
    req = build_request(env.get("IN_PERSON", ""), env.get("IN_CLAIM", ""), env.get("IN_ALSO", ""),
                        env.get("IN_ANYONE") == "true", env.get("IN_STRICT") == "true",
                        int(env.get("IN_MAX") or 30), env.get("IN_LANGS") or "en,fr", secret)
    try:
        publish_fn(topic, json.dumps(req, ensure_ascii=False))
    except Exception as e:  # noqa: BLE001
        print(f"Envoi au téléphone impossible ({e}).")
        return where != "phone"
    print(f"Demande {req['id']} envoyée au téléphone, attente de l'accusé de réception ({wait}s)...")
    t0 = time.time()
    while time.time() - t0 < wait:
        sleep(5)
        try:
            if any(req["id"] in m for m in poll_fn(f"{topic}-ack")):
                print("Le téléphone a pris la recherche en charge : GitHub n'a rien à faire.")
                return False
        except Exception as e:  # noqa: BLE001
            print(f"(lecture de l'accusé : {e})")
    print("Pas de réponse du téléphone.")
    return where != "phone"


# ---------------------------------------------------------------- côté téléphone
def _notify(title: str, text: str) -> None:
    try:
        subprocess.run(["termux-notification", "--title", title, "--content", text], timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


def handle(d: dict, repo: Path, conf: Path) -> None:
    args = to_args(d)
    if args is None:
        print("Demande invalide ignorée.")
        return
    env = {**os.environ, "QF_CACHE_DIR": str(conf / "cache")}
    log = conf / "last.log"
    _notify("Quote Finder", f"Recherche lancée : {d['person']}")
    with open(log, "w", encoding="utf-8") as f:
        try:
            rc = subprocess.run([sys.executable, "-m", "quote_finder", *args, "--max-minutes=40", f"--out={repo / 'results'}"],
                                cwd=repo, env=env, stdout=f, stderr=subprocess.STDOUT, timeout=55 * 60).returncode
        except subprocess.TimeoutExpired:
            rc = 124
    outs = sorted((repo / "results").glob("*/"), key=lambda p: p.stat().st_mtime, reverse=True)
    if rc != 0 or not outs:
        tail = ""
        try:
            tail = [l for l in log.read_text(encoding="utf-8").splitlines() if "ÉCHEC" in l or "ERROR" in l][-1][:140]
        except (OSError, IndexError):
            pass
        _notify("Quote Finder — échec", tail or "voir ~/.quote_finder/last.log")
        return
    for cmd in (["git", "add", "results"], ["git", "commit", "-qm", f"résultats (téléphone) {d['id']}"],
                ["git", "pull", "--rebase", "-q"], ["git", "push", "-q"]):
        subprocess.run(cmd, cwd=repo, timeout=120)
    dl = Path.home() / "storage" / "downloads"
    if dl.is_dir():
        try:
            (dl / f"quote_finder_{outs[0].name}.md").write_bytes((outs[0] / "report.md").read_bytes())
        except OSError:
            pass
    _notify("Quote Finder", f"Terminé : {d['person']} — rapport dans Téléchargements et sur GitHub")


def listen(repo: Path, conf: Path) -> None:
    import urllib.parse
    topic, secret = os.environ["NTFY_TOPIC"], os.environ["PHONE_SECRET"]
    last = conf / "ntfy_last"
    seen: set[str] = set()
    since = last.read_text().strip() if last.exists() else str(int(time.time()) - 300)
    backoff = 5
    print(f"Écoute du sujet ntfy… (Ctrl+C pour arrêter)")
    while True:
        try:
            url = f"{NTFY}/{topic}/json?" + urllib.parse.urlencode({"since": since})
            with urllib.request.urlopen(url, timeout=120) as r:  # ntfy envoie un « keepalive » toutes les 45 s
                backoff = 5
                for raw in r:  # ligne par ligne, dès son arrivée (pas de tampon comme avec requests.iter_lines)
                    line = raw.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    if e.get("id"):
                        since = e["id"]
                        last.write_text(since)
                    if e.get("event") != "message":
                        continue
                    try:
                        d = json.loads(e.get("message", ""))
                    except ValueError:
                        continue
                    if not isinstance(d, dict) or not verify(d, secret) or d.get("id") in seen:
                        print("Message ignoré (signature/âge/doublon).")
                        continue
                    seen.add(d["id"])
                    publish(f"{topic}-ack", json.dumps({"id": d["id"]}))  # accusé immédiat
                    handle(d, repo, conf)
        except Exception as ex:  # noqa: BLE001 - réseau coupé, veille du téléphone...
            print(f"Connexion perdue ({type(ex).__name__}), nouvel essai dans {backoff}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)


def main() -> int:
    repo = Path(__file__).resolve().parent.parent
    conf = Path(os.environ.get("QF_CONF", Path.home() / ".quote_finder"))
    conf.mkdir(parents=True, exist_ok=True)
    if not os.environ.get("NTFY_TOPIC") or not os.environ.get("PHONE_SECRET"):
        print("NTFY_TOPIC / PHONE_SECRET manquants : lance  qf phone-setup")
        return 1
    listen(repo, conf)
    return 0


if __name__ == "__main__":
    sys.exit(main())
