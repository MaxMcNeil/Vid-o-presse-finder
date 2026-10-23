import json
import time

from quote_finder import phone

S = "secret123"


def req(**kw):
    base = dict(person="Bill Clinton", claim="Saddam a des ADM", also="", anyone=False, strict=False, max_videos=30, langs="en,fr")
    base.update(kw)
    return phone.build_request(**base, secret=S)


def test_signature_roundtrip_and_tamper():
    d = req()
    assert phone.verify(d, S)
    assert not phone.verify(d, "autre")
    d2 = dict(d, claim="autre chose")
    assert not phone.verify(d2, S)


def test_stale_and_replay_window():
    d = req()
    assert not phone.verify(d, S, now=time.time() + 3600)


def test_args_are_safe():
    a = phone.to_args(req(person="--evil", claim="x; rm -rf ~", also="a,b"))
    assert a[0] == "--person=--evil" and a[1] == "--claim=x; rm -rf ~"  # une seule chaîne chacun, jamais interprétée
    assert phone.to_args(req(max_videos=999)) is None
    assert phone.to_args(req(langs="en;rm")) is None
    assert phone.to_args(req(person="")) is None
    assert "--anyone" in phone.to_args(req(anyone=True))


def test_route_phone_acks():
    sent = []
    env = {"WHERE": "phone-then-github", "NTFY_TOPIC": "t", "PHONE_SECRET": S, "IN_PERSON": "A", "IN_CLAIM": "B",
           "IN_ANYONE": "true", "IN_MAX": "10", "IN_LANGS": "fr"}
    pub = lambda topic, text: sent.append(json.loads(text))
    poll = lambda topic: [json.dumps({"id": sent[0]["id"]})]
    assert phone.route(env, pub, poll, wait=20, sleep=lambda s: None) is False
    assert phone.verify(sent[0], S) and sent[0]["anyone"] is True


def test_route_falls_back_to_github_without_ack():
    env = {"WHERE": "phone-then-github", "NTFY_TOPIC": "t", "PHONE_SECRET": S, "IN_PERSON": "A", "IN_CLAIM": "B"}
    t = [0]
    def sl(s): t[0] += s
    assert phone.route(env, lambda *a: None, lambda *a: [], wait=0.01, sleep=lambda s: None) is True
    assert phone.route({**env, "WHERE": "phone"}, lambda *a: None, lambda *a: [], wait=0.01, sleep=lambda s: None) is False
    assert phone.route({**env, "WHERE": "github"}, None, None) is True
    assert phone.route({"WHERE": "phone-then-github"}, None, None) is True  # secrets absents
