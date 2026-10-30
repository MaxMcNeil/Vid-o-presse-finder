"""Clés partagées (…_KEYS_PARTAGE) : réserve, utilisées seulement quand les clés principales sont indisponibles."""
import time

from quote_finder.keys import KeyPool, keys_from_env, shared_keys


def test_shared_keys_only_from_partage(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEYS", "a\nb")
    monkeypatch.setenv("GEMINI_API_KEYS_PLUS", "c")
    monkeypatch.setenv("GEMINI_API_KEYS_PARTAGE", "x\ny\na")      # « a » est aussi une clé à soi : pas une réserve
    assert keys_from_env("GEMINI") == ["a", "b", "c", "x", "y"]
    assert shared_keys("GEMINI") == {"x", "y"}


def test_main_keys_first_then_reserve():
    p = KeyPool("gemini", ["a", "b", "x"], reserve={"x"})
    got = {p.acquire() for _ in range(6)}
    assert got == {"a", "b"}                         # tant que a et b marchent, x n'est jamais pris
    p.cooldown("a", 3600)
    p.cooldown("b", 3600)                           # a et b épuisées / bloquées longtemps
    assert p.acquire() == "x"


def test_waits_briefly_for_a_main_key_instead_of_reserve():
    p = KeyPool("gemini", ["a", "x"], reserve={"x"})
    p.cooldown("a", 0.5)                            # « a » revient dans 0,5 s : on l'attend
    t = time.time()
    assert p.acquire() == "a" and time.time() - t >= 0.4


def test_no_wait_mode_uses_reserve():
    p = KeyPool("gemini", ["a", "x"], reserve={"x"})
    p.cooldown("a", 0.5)
    assert p.acquire(wait=False) == "x"


def test_only_shared_keys_are_used_normally():
    p = KeyPool("gemini", ["x", "y"], reserve={"x", "y"})
    assert p.acquire() in ("x", "y")
