"""Cas réels du lancement du 25/10/2026 (famine / Ormuz) : chaque anomalie constatée a son garde-fou."""
import json

from quote_finder import batch, registry, report, scan
from quote_finder.models import Hit


def H(t, te, q, role="journalist", who="other_named", name="", conf=0.95, verdict="literal", timing="ia-affiné"):
    return Hit(t, q, verdict, who, name, conf, t_end=te, speaker_role=role, timing=timing)


def test_no_fake_statements():
    hits = [H(41, 46, "Could this conflict trigger the next global food shock?"),          # question du présentateur
            H(0, 4, "FARMERS WARN OF FOOD SHORTAGES", role="other"),                       # bandeau à l'écran
            H(1202, 1223, "Ahmed Idris, Al Jazeera, for Counting the Cost in Abuja, Nigeria."),  # signature seule
            H(500, 512, "Food prices could rise sharply across Europe this winter, the minister warned.", role="official")]
    out = scan.tidy_hits(hits, 1700)
    assert [h.t for h in out] == [500]


def test_signoff_is_removed_from_a_real_quote():
    h = H(1202, 1223, "Already they're struggling with the cost of seeds and fertilizer. Ahmed Idris, Al Jazeera, for Counting the Cost in Abuja, Nigeria.")
    out = scan.tidy_hits([h], 1700)
    assert out and "Al Jazeera" not in out[0].quote and out[0].quote.endswith("fertilizer.")


def test_overlapping_clips_never_both_kept():
    a = H(19, 45, "Food prices are up and fertilizer costs are soaring across the world now.")
    b = H(38, 74, "The next global food crisis may not begin in farms or supermarkets, it is unfolding at sea.", conf=0.9)
    c = H(104, 121, "Now, how have different members of the college contributed to this debate?", role="official")
    d = H(102, 150, "In relation to Commissioner Wojciechowski, he advocates consistently for European farmers.", role="official")
    out = scan.tidy_hits([a, b, c, d], 1700)
    spans = sorted((h.t, h.t_end) for h in out)
    assert all(spans[i][1] <= spans[i + 1][0] for i in range(len(spans) - 1))  # jamais de chevauchement


def test_aberrant_long_clip_is_tightened():
    # Kallas : 2:14 → 5:23 (189 s) pour ~40 mots
    q = ("During the weekend I also had talks with UN Secretary General Antonio Guterres about whether it was possible "
         "to also have the same kind of initiative like we had the Black Sea initiative to get the grain out")
    out = scan.tidy_hits([H(134, 323, q, role="official", name="Kaja Kallas")], 755)
    assert 10 < out[0].t_end - out[0].t < 40
    # morceaux éloignés collés avec « ... » : seul le 1er est gardé
    q2 = "We need to take into account the sensitivities of agricultural sectors... including safeguards for the EU market as a whole"
    out2 = scan.tidy_hits([H(29, 110, q2, role="official")], 131)
    assert out2[0].t_end - out2[0].t < 20 and "including" not in out2[0].quote


def test_placeholder_speaker_names_are_dropped():
    assert scan.clean_name("target") == "" and scan.clean_name("Journaliste") == "" and scan.clean_name("Kaja Kallas") == "Kaja Kallas"


def test_shorts_links_are_valid():
    assert report.yt_watch("https://www.youtube.com/shorts/S9yPz-GG25U") == "https://www.youtube.com/watch?v=S9yPz-GG25U"
    assert report.ts_url("https://www.youtube.com/shorts/S9yPz-GG25U", "youtube", 12) == "https://www.youtube.com/watch?v=S9yPz-GG25U&t=12s"
    h = H(10, 20, "x y z")
    assert report.clip_link("https://www.youtube.com/shorts/S9yPz-GG25U", "youtube", h).startswith("https://www.youtube.com/embed/S9yPz-GG25U?start=10")


def test_sources_always_include_international_agencies_and_subject_sources():
    for countries in (["UA", "IR", "FR"], ["IT", "IR", "OM"], ["FR"], []):
        doms = [s.domain for s in registry.for_search(countries, ["fr", "en"], ["fao.org", "un.org"], 8)]
        assert "afp.com" in doms and "fao.org" in doms and len(doms) == 8
    eu = [s.domain for s in registry.for_search(["EU"], ["fr", "en"], [], 8)]
    assert eu[0] == "ec.europa.eu" and "elysee.fr" not in eu
    assert "FAO" in registry.youtube_channels(["ORG"], ["en"], 3)
    assert [s.domain for s in registry.for_search(["GCC"], ["ar"], [], 8)][:2] == ["spa.gov.sa", "wam.ae"]


def test_cancelled_search_is_listed_not_lost(tmp_path):
    lot = tmp_path / "2026-10-25_2019_n22"
    (lot / "01-a").mkdir(parents=True)
    (lot / "01-a" / "results.json").write_text(json.dumps({"target": "A", "claim": "b", "videos": [], "articles": []}))
    m = json.dumps([{"id": "01-a", "target": "A", "claim": "b"}, {"id": "02-bernard-valluis", "target": "Bernard Valluis",
                    "claim": "abandon du stockage alimentaire", "type": "personne"}])
    assert batch.mark_missing(lot, m) == ["02-bernard-valluis"]
    idx = batch.merge(lot).read_text()
    assert "Bernard Valluis" in idx and "interrompue" in idx


def test_unsupported_script_dropped_arabic_kept():
    g = H(7, 20, "यूरोपमा हिट वेभ, अलग्नोनी असर खादय संकट ना ऐन्धान युद्ध ना कारने")
    a = H(30, 40, "الأمن الغذائي في أوروبا مهدد بسبب الحرب", role="official")
    assert [h.t for h in scan.tidy_hits([g, a], 100)] == [30]
