"""Réseaux sociaux en appui des recherches : Telegram (chaînes publiques) et Reddit. Pas de X, TikTok ni Instagram.

Telegram (secrets TG_API_ID, TG_API_HASH, TG_SESSION — compte secondaire conseillé) :
  1. chaînes publiques liées au sujet (recherche par nom, comme dans l'appli) ;
  2. dans chacune, messages contenant les mots-clés de la recherche.
  La recherche de mots dans TOUT Telegram n'existe qu'avec Premium : elle n'est pas utilisée.
  Une même clé de session utilisée en même temps depuis deux machines est DÉTRUITE par Telegram : un verrou
  (fichier sur la machine + étiquette « verrou-telegram » dans le dépôt GitHub) garantit un seul usage à la fois.

Reddit : flux publics (sans compte : Reddit n'accorde plus de clés en libre-service). Reddit bloque souvent les
serveurs GitHub : alors la source est sautée et c'est écrit dans le rapport.

Règle d'or : rien ici ne peut faire échouer la recherche. Chaque problème est écrit dans STATUS (rapport, ETAT.md,
PLAN.md du montage) — jamais d'échec silencieux."""
from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
import logging
import os
import re
import time
import xml.etree.ElementTree as ET

import requests

from . import scan
from .util import call_with_timeout, normalize

log = logging.getLogger("qf.social")
STATUS: dict[str, str] = {}
UA = "Mozilla/5.0 (compatible; vid-o-presse-finder/1.0; +https://github.com/MaxMcNeil/Vid-o-presse-finder)"
LOCK_TAG = "verrou-telegram"
LOCK_STALE = 20 * 60          # un verrou plus vieux (machine arrêtée net) est considéré comme abandonné
MAX_CHANNELS = 8
PER_CHANNEL = 6


def reset() -> None:
    STATUS.clear()


# ------------------------------------------------------------------ mots-clés
def queries(plan, o) -> list[str]:
    """2-3 requêtes courtes (Telegram et Reddit cherchent mal les phrases longues)."""
    out = []
    alias = (plan.target_aliases or [o.target])[0] if (plan.target_aliases or o.target) else ""
    key = next((g[0] for g in plan.term_groups or [] if g and normalize(g[0]) != normalize(alias)), "")
    for q in (plan.site_query, f"{o.target} {key}".strip(), o.target):
        q = " ".join((q or "").split()[:6]).strip()
        if q and normalize(q) not in {normalize(x) for x in out}:
            out.append(q)
    return out[:3]


def words(plan, o) -> list[str]:
    """Mots cherchés DANS les messages d'une chaîne : la cible, puis le terme le plus parlant."""
    out = []
    for w in [*(plan.target_aliases or [])[:2], o.target, *(g[0] for g in (plan.term_groups or []) if g)]:
        w = (w or "").strip()
        if w and len(w) >= 3 and normalize(w) not in {normalize(x) for x in out}:
            out.append(w)
    return out[:3]


def relevant(text: str, plan, o) -> bool:
    if not text or len(text) < 40:
        return False
    if scan.group_score(text, plan.term_groups or []) < 1:
        return False
    if o.target_type in ("sujet", "pays"):
        return True
    n = normalize(text)
    return any(a and normalize(a) in n for a in (plan.target_aliases or [o.target]))


def _since_ok(date: str, since: str) -> bool:
    return not since or not date or date[:10] >= since


# ------------------------------------------------------------------ Reddit
def reddit(qs: list[str], since: str = "", http=None, timeout: float = 20) -> list[dict]:
    s = http or requests.Session()
    hdr = {"User-Agent": UA, "Accept": "application/json"}
    out, seen, codes = [], set(), []
    t = "year" if (since and since >= (dt.date.today() - dt.timedelta(days=365)).isoformat()) else "all"
    for q in qs[:2]:
        got = False
        for base in ("https://www.reddit.com", "https://old.reddit.com"):
            try:
                r = s.get(f"{base}/search.json", params={"q": q, "sort": "relevance", "t": t, "limit": 25, "raw_json": 1},
                          headers=hdr, timeout=timeout)
            except requests.RequestException as e:
                codes.append(type(e).__name__)
                continue
            if r.status_code != 200:
                codes.append(f"HTTP {r.status_code}")
                continue
            try:
                kids = r.json()["data"]["children"]
            except (ValueError, KeyError, TypeError):
                codes.append("réponse illisible")
                continue
            got = True
            for k in kids:
                d = k.get("data") or {}
                if d.get("over_18") or (d.get("score") or 0) < 3 or not d.get("permalink"):
                    continue
                url = "https://www.reddit.com" + d["permalink"]
                if url in seen:
                    continue
                seen.add(url)
                date = dt.datetime.fromtimestamp(float(d.get("created_utc") or 0), dt.timezone.utc).date().isoformat()
                out.append({"platform": "reddit", "channel": f"r/{d.get('subreddit', '?')}",
                            "text": (d.get("title") or "") + (" — " + d["selftext"][:300] if d.get("selftext") else ""),
                            "url": url, "date": date, "score": int(d.get("score") or 0),
                            "comments": int(d.get("num_comments") or 0)})
            break
        if not got:  # flux RSS : parfois accepté quand le JSON est refusé
            out += [x for x in _reddit_rss(s, q, hdr, timeout, codes) if x["url"] not in seen]
            seen |= {x["url"] for x in out}
    if not out and codes:
        STATUS["reddit"] = f"bloqué par Reddit depuis GitHub ({', '.join(sorted(set(codes)))}) : source ignorée"
    return out


def _reddit_rss(s, q: str, hdr: dict, timeout: float, codes: list) -> list[dict]:
    try:
        r = s.get("https://www.reddit.com/search.rss", params={"q": q, "sort": "relevance"},
                  headers={**hdr, "Accept": "application/atom+xml"}, timeout=timeout)
    except requests.RequestException as e:
        codes.append(f"RSS {type(e).__name__}")
        return []
    if r.status_code != 200:
        codes.append(f"RSS HTTP {r.status_code}")
        return []
    try:
        root = ET.fromstring(r.content)
    except ET.ParseError:
        codes.append("RSS illisible")
        return []
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for e in root.findall("a:entry", ns):
        link = e.find("a:link", ns)
        url = link.get("href") if link is not None else ""
        if "/comments/" not in (url or ""):
            continue
        cat = e.find("a:category", ns)
        out.append({"platform": "reddit", "channel": f"r/{cat.get('term')}" if cat is not None else "reddit",
                    "text": (e.findtext("a:title", "", ns) or "").strip(), "url": url,
                    "date": (e.findtext("a:updated", "", ns) or "")[:10], "score": 0, "comments": 0})
    return out


# ------------------------------------------------------------------ verrou (une seule session Telegram à la fois)
def _gh():
    tok = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY") or os.environ.get("REPO")
    if not tok or not repo:
        return None
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json",
                      "X-GitHub-Api-Version": "2022-11-28", "User-Agent": UA})
    return s, f"https://api.github.com/repos/{repo}"


def _gh_lock_age(s, api) -> float | None:
    r = s.get(f"{api}/git/ref/tags/{LOCK_TAG}", timeout=20)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    obj = r.json()["object"]
    if obj.get("type") != "tag":
        return LOCK_STALE + 1  # étiquette inattendue : considérée comme abandonnée
    t = s.get(f"{api}/git/tags/{obj['sha']}", timeout=20)
    t.raise_for_status()
    try:
        return time.time() - float(json.loads(t.json().get("message") or "{}").get("t", 0))
    except ValueError:
        return LOCK_STALE + 1


def _gh_acquire(s, api) -> str:
    sha = os.environ.get("GITHUB_SHA")
    if not sha:
        r = s.get(api, timeout=20)
        r.raise_for_status()
        b = s.get(f"{api}/git/ref/heads/{r.json()['default_branch']}", timeout=20)
        b.raise_for_status()
        sha = b.json()["object"]["sha"]
    msg = json.dumps({"t": time.time(), "run": os.environ.get("GITHUB_RUN_ID", "local")})
    tg = s.post(f"{api}/git/tags", json={"tag": LOCK_TAG, "message": msg, "object": sha, "type": "commit"}, timeout=20)
    tg.raise_for_status()
    r = s.post(f"{api}/git/refs", json={"ref": f"refs/tags/{LOCK_TAG}", "sha": tg.json()["sha"]}, timeout=20)
    if r.status_code == 422:      # déjà pris par un autre lancement
        return ""
    r.raise_for_status()
    return tg.json()["sha"]


@contextlib.contextmanager
def telegram_lock(max_wait: float):
    """Rend {"ok": True si le verrou est obtenu, "keep": False}. Jamais de blocage au-delà de max_wait.
    keep = True : la session n'a pas pu être fermée proprement → le verrou du dépôt est laissé en place (il expire
    seul après 20 min) pour qu'aucun autre lancement ne s'en serve pendant ce temps."""
    import fcntl
    f = open(os.path.join(os.environ.get("RUNNER_TEMP") or "/tmp", "qf-telegram.lock"), "w")
    end = time.time() + max(0.0, max_wait)
    have_file = False
    while True:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            have_file = True
            break
        except OSError:
            if time.time() > end:
                break
            time.sleep(3)
    mine, gh = "", _gh()
    ok = have_file
    st = {"ok": False, "keep": False}
    try:
        if ok and gh:
            s, api = gh
            ok = False
            while True:
                age = _gh_lock_age(s, api)
                if age is not None and age > LOCK_STALE:
                    log.warning("verrou Telegram abandonné (%.0f min) : libéré", age / 60)
                    s.delete(f"{api}/git/refs/tags/{LOCK_TAG}", timeout=20)
                    age = None
                if age is None:
                    mine = _gh_acquire(s, api)
                    if mine:
                        ok = True
                        break
                if time.time() > end:
                    break
                time.sleep(10)
        st["ok"] = ok
        yield st
    finally:
        if mine and gh and not st["keep"]:
            s, api = gh
            with contextlib.suppress(Exception):
                r = s.get(f"{api}/git/ref/tags/{LOCK_TAG}", timeout=20)
                if r.ok and r.json()["object"]["sha"] == mine:   # on ne libère que NOTRE verrou
                    s.delete(f"{api}/git/refs/tags/{LOCK_TAG}", timeout=20)
        if have_file:
            with contextlib.suppress(OSError):
                fcntl.flock(f, fcntl.LOCK_UN)
        f.close()


# ------------------------------------------------------------------ Telegram
def tg_config() -> tuple[int, str, str] | str:
    """(api_id, api_hash, session) ou message d'erreur lisible."""
    vals = {k: (os.environ.get(k) or "").strip() for k in ("TG_API_ID", "TG_API_HASH", "TG_SESSION")}
    missing = [k for k, v in vals.items() if not v]
    if len(missing) == 3:
        return "non configuré (secrets TG_API_ID, TG_API_HASH, TG_SESSION absents)"
    if missing:
        return f"configuration incomplète : secret(s) {', '.join(missing)} absent(s)"
    if not vals["TG_API_ID"].isdigit():
        return "TG_API_ID invalide (ce doit être un nombre, voir my.telegram.org)"
    if not re.fullmatch(r"[0-9a-fA-F]{32}", vals["TG_API_HASH"]):
        return "TG_API_HASH invalide (32 caractères, voir my.telegram.org)"
    return int(vals["TG_API_ID"]), vals["TG_API_HASH"], vals["TG_SESSION"]


async def _tg_search(cfg, qs, ws, since, plan, o, box: list, note: dict):
    from telethon import TelegramClient, functions
    from telethon.sessions import StringSession
    from telethon.tl.types import Channel
    api_id, api_hash, sess = cfg
    try:
        ss = StringSession(re.sub(r"\s+", "", sess))   # retours à la ligne / espaces d'un copier-coller retirés
    except ValueError:
        raise PermissionError("TG_SESSION illisible (copie incomplète ?)") from None
    client = TelegramClient(ss, api_id, api_hash, connection_retries=2, request_retries=2,
                            flood_sleep_threshold=15, timeout=20, receive_updates=False)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise PermissionError("session non autorisée")
        chans = {}
        for q in qs:
            res = await client(functions.contacts.SearchRequest(q=q, limit=20))
            for ch in res.chats:
                if isinstance(ch, Channel) and ch.broadcast and getattr(ch, "username", None) \
                        and not getattr(ch, "scam", False) and not getattr(ch, "fake", False):
                    chans[ch.id] = ch
        top = sorted(chans.values(), key=lambda c: -(getattr(c, "participants_count", 0) or 0))[:MAX_CHANNELS]
        note["channels"] = len(top)
        seen = set()
        for ch in top:
            for w in ws[:2]:
                async for m in client.iter_messages(ch, search=w, limit=PER_CHANNEL):
                    text = (m.message or "").strip()
                    url = f"https://t.me/{ch.username}/{m.id}"
                    date = m.date.date().isoformat() if m.date else ""
                    if url in seen or not _since_ok(date, since) or not relevant(text, plan, o):
                        continue
                    seen.add(url)
                    box.append({"platform": "telegram", "channel": f"@{ch.username}", "text": text[:600], "url": url,
                                "date": date, "score": int(m.views or 0), "comments": 0,
                                "media": "vidéo" if getattr(m, "video", None) else ("photo" if m.photo else "")})
    finally:
        await client.disconnect()


def telegram(plan, o, budget: float) -> list[dict]:
    cfg = tg_config()
    if isinstance(cfg, str):
        STATUS["telegram"] = cfg
        return []
    try:
        import telethon  # noqa: F401
    except ImportError:
        STATUS["telegram"] = "module telethon absent (requirements.txt) : source ignorée"
        return []
    box: list[dict] = []
    note: dict = {}
    with telegram_lock(max_wait=min(240.0, budget / 2)) as lk:
        if not lk["ok"]:
            STATUS["telegram"] = "session déjà utilisée par un autre lancement (verrou) : source sautée cette fois"
            return []
        left = max(30.0, budget - 10)

        def go():
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(asyncio.wait_for(
                    _tg_search(cfg, queries(plan, o), words(plan, o), o.since, plan, o, box, note), timeout=left))
            finally:
                loop.close()
        try:
            call_with_timeout(go, left + 20)
            STATUS["telegram"] = f"ok : {len(box)} message(s) dans {note.get('channels', 0)} chaîne(s) publique(s)"
        except Exception as e:  # noqa: BLE001 — chaque cas a son message clair
            if isinstance(e, TimeoutError) and "plus de" in str(e):
                lk["keep"] = True   # connexion peut-être encore ouverte : verrou gardé jusqu'à expiration
            STATUS["telegram"] = _tg_error(e, len(box))
            lvl = log.error if "régénère" in STATUS["telegram"] else log.warning
            lvl("Telegram : %s", STATUS["telegram"])
    return box


def _tg_error(e: Exception, n: int) -> str:
    name = type(e).__name__
    part = f" ({n} message(s) gardé(s))" if n else ""
    if name == "AuthKeyDuplicatedError":
        return ("⚠ session détruite par Telegram (utilisée en même temps ailleurs) : régénère TG_SESSION "
                "(script Termux) et utilise une session différente par dépôt")
    if name in ("AuthKeyUnregisteredError", "SessionRevokedError", "SessionExpiredError", "UserDeactivatedError",
                "UserDeactivatedBanError", "PermissionError"):
        why = str(e) if name == "PermissionError" else name
        return f"⚠ session refusée ({why}) : régénère TG_SESSION (script Termux)"
    if name == "FloodWaitError":
        return f"limite Telegram atteinte (attendre {getattr(e, 'seconds', '?')} s) : résultats partiels{part}"
    if name in ("TimeoutError", "CancelledError"):
        return f"temps imparti écoulé : résultats partiels{part}"
    if name in ("ApiIdInvalidError",):
        return "⚠ TG_API_ID / TG_API_HASH refusés : vérifie-les sur my.telegram.org"
    return f"erreur {name} : {str(e)[:120]}{part}"


# ------------------------------------------------------------------ ensemble
def search(plan, o, http=None, budget: float = 180.0) -> list[dict]:
    """Messages Telegram + Reddit pertinents, triés (plus vus / plus votés d'abord). Ne lève jamais d'exception."""
    reset()
    if os.environ.get("QF_SOCIAL", "1").strip().lower() in ("0", "non", "no", "false", "off"):
        STATUS["telegram"] = STATUS["reddit"] = "désactivé (QF_SOCIAL=0)"
        return []
    t0 = time.time()
    posts: list[dict] = []
    try:
        rd = [p for p in reddit(queries(plan, o), o.since, http) if _since_ok(p["date"], o.since) and relevant(p["text"], plan, o)]
        STATUS.setdefault("reddit", f"ok : {len(rd)} discussion(s) pertinente(s)")
        posts += rd
    except Exception as e:  # noqa: BLE001
        STATUS["reddit"] = f"erreur {type(e).__name__} : source ignorée"
    try:
        posts += telegram(plan, o, budget - (time.time() - t0))
    except Exception as e:  # noqa: BLE001
        STATUS["telegram"] = f"erreur {type(e).__name__} : {str(e)[:100]}"
    posts.sort(key=lambda p: -p.get("score", 0))
    log.info("Réseaux sociaux : Telegram → %s · Reddit → %s", STATUS.get("telegram"), STATUS.get("reddit"))
    return posts[:20]


# ------------------------------------------------------------------ appui / contre
def classify(llm, o, items: list[dict]) -> None:
    """items : [{"text": …}] → ajoute "position" : appui | contre | neutre ("?" si l'IA ne répond pas)."""
    if not items:
        return
    claim = f"{o.target} — {o.claim}" if o.claim else o.target
    lines = "\n".join(f"[{i}] {(x.get('text') or '')[:280]}" for i, x in enumerate(items[:40]))
    prompt = (f"L'auteur d'une vidéo affirme : « {claim} ».\n"
              "Pour chaque élément ci-dessous (titre d'article ou message), dis s'il APPUIE cette affirmation, s'il la "
              "CONTREDIT (dément, nuance fortement, chiffres contraires), ou s'il est NEUTRE / hors sujet.\n"
              f"{lines}\n"
              'Réponds en JSON : {"positions": [{"i": numéro, "p": "appui|contre|neutre"}]}')
    try:
        j = llm.generate_json(prompt)
        got = {int(x["i"]): str(x.get("p", "")).strip().lower() for x in (j or {}).get("positions", [])
               if str(x.get("i", "")).lstrip("-").isdigit()}
    except Exception as e:  # noqa: BLE001
        STATUS["positions"] = f"non classées (IA indisponible : {type(e).__name__})"
        got = {}
    for i, x in enumerate(items):
        p = got.get(i, "?")
        x["position"] = p if p in ("appui", "contre", "neutre") else "?"
    n = sum(1 for x in items if x["position"] == "contre")
    if n:
        STATUS["positions"] = f"{n} élément(s) qui contredisent l'affirmation (listés à part)"


# ------------------------------------------------------------------ diagnostic (workflow « Diagnostic »)
def check() -> list[str]:
    """Teste Telegram (connexion + session) et Reddit (une recherche) — lignes lisibles pour state/DIAGNOSTIC.md."""
    out = []
    cfg = tg_config()
    if isinstance(cfg, str):
        out.append(f"Telegram : {cfg}")
    else:
        async def who():
            from telethon import TelegramClient
            from telethon.sessions import StringSession
            try:
                ss = StringSession(re.sub(r"\s+", "", cfg[2]))
            except ValueError:
                raise PermissionError("TG_SESSION illisible (copie incomplète ?)") from None
            c = TelegramClient(ss, cfg[0], cfg[1], connection_retries=2, timeout=20, receive_updates=False)
            await c.connect()
            try:
                if not await c.is_user_authorized():
                    raise PermissionError("session non autorisée")
                me = await c.get_me()
                return f"connecté (compte {'@' + me.username if me.username else me.first_name or me.id})"
            finally:
                await c.disconnect()
        try:
            with telegram_lock(max_wait=120) as lk:
                if not lk["ok"]:
                    out.append("Telegram : session occupée par une recherche en cours (verrou) — réessaie plus tard")
                else:
                    loop = asyncio.new_event_loop()
                    try:
                        out.append("Telegram : ✅ " + loop.run_until_complete(asyncio.wait_for(who(), 60)))
                    finally:
                        loop.close()
        except Exception as e:  # noqa: BLE001
            out.append("Telegram : ❌ " + _tg_error(e, 0))
    reset()
    got = reddit(["France"])
    out.append(f"Reddit : ✅ accessible ({len(got)} résultat(s) test)" if got
               else f"Reddit : ❌ {STATUS.get('reddit', 'aucun résultat')}")
    return out


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.WARNING)
    if sys.argv[1:2] == ["check"]:
        for line in check():
            print(line)
