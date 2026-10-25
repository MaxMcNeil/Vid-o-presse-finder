import json

from quote_finder import phone_precise, report
from quote_finder.models import Hit, Plan, Segment, VideoCandidate
from quote_finder.pipeline import Options, VideoResult


def _lot(tmp_path):
    plan = Plan("x", [["microsoft"]], ["suisse"], [], [], ["de", "fr"], target_type="pays")
    v = VideoCandidate("youtube", "A-cipLy-c7s", "https://www.youtube.com/watch?v=A-cipLy-c7s", "Bund plant Schritt", "",
                       "Franz Grueter", "2026-04-20", 246, authority=0.45)
    h1 = Hit(9, "Die Software Microsoft 365 wurde erst vor wenigen Monaten installiert", "literal", "other_named", "", 0.95, t_end=50)
    h2 = Hit(136, "Bei Bundesverwaltung und Schweizer Botschaften sind etwa 54.000 Arbeitsplätze ausgerüstet", "literal",
             "other_named", "", 0.9, t_end=157, timing="ia-affiné")
    h3 = Hit(200, "phrase inventée qui ne figure nulle part", "literal", "other_named", "", 0.9, t_end=210)
    import datetime as dt
    lot = tmp_path / "results" / f"{dt.date.today().isoformat()}_0900_n1"
    report.build(Options("La Suisse", "abandonne Microsoft", target_type="pays", speakers="tous"), plan,
                 [VideoResult(v, [h1, h2, h3], "gemini-video")], [], lot / "01-la-suisse", search_id="01-la-suisse")
    return lot


SEGS = [Segment(0.0, "Überraschende Kehrtwende"), Segment(8.4, "die Software Microsoft 365 wurde erst vor"),
        Segment(11.9, "wenigen Monaten installiert"), Segment(15.2, "es gibt nun Bedenken"),
        Segment(134.6, "bei Bundesverwaltung und Schweizer Botschaften"), Segment(138.0, "sind etwa 54.000 Arbeitsplätze ausgerüstet"),
        Segment(142.5, "trotzdem will der Bund")]


def test_phone_recalibrates_on_subtitles_and_marks_attempts(tmp_path, monkeypatch):
    lot = _lot(tmp_path)
    calls = []
    st = phone_precise.process(tmp_path / "results", pause=(0, 0), fetch=lambda vid, langs: calls.append(vid) or [SEGS])
    assert st["recalés"] == 2 and st["non_retrouvés"] == 1 and calls == ["A-cipLy-c7s"]
    m = json.loads((lot / "01-la-suisse" / "montage.json").read_text())["clips"]
    starts = sorted(c["start"] for c in m)
    assert starts == [8.4, 134.6, 200]  # le passage introuvable garde l'horodatage IA (non dégradé)
    assert [c["timing"] for c in sorted(m, key=lambda c: c["start"])] == ["sous-titres", "sous-titres", "ia"]
    assert next(c for c in m if c["start"] == 8.4)["end"] == 15.2 and next(c for c in m if c["start"] == 8.4)["in"] == 7.4
    assert "## Précision (téléphone)" in (lot / "01-la-suisse" / "report.md").read_text()
    assert json.loads((lot / "montage.json").read_text())["clips"]  # montage global du lot reconstruit
    # 2e passage : rien à refaire (tentatives mémorisées)
    st2 = phone_precise.process(tmp_path / "results", pause=(0, 0), fetch=lambda vid, langs: calls.append(vid) or [SEGS])
    assert st2["videos"] == 0 and calls == ["A-cipLy-c7s"]


def test_blocked_stops_cleanly_and_retries_later(tmp_path, monkeypatch):
    _lot(tmp_path)

    def blocked(vid, langs):
        raise phone_precise.Blocked("RequestBlocked")
    st = phone_precise.process(tmp_path / "results", pause=(0, 0), fetch=blocked)
    assert st["bloqué"] and st["recalés"] == 0 and st["lots"] == []
    st2 = phone_precise.process(tmp_path / "results", pause=(0, 0), fetch=lambda v, l: [SEGS])
    assert st2["recalés"] == 2  # retenté au passage suivant
