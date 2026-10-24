from quote_finder import precise, report
from quote_finder.models import Hit


def H(t, te, q="Die Software Microsoft 365 wurde erst vor wenigen Monaten installiert"):
    return Hit(t, q, "literal", "other_named", "", 0.9, t_end=te)


class L:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def generate_json(self, prompt, video_url=None, video_clip=None, **k):
        self.calls.append(video_clip)
        return self.answers.pop(0)


def test_refine_snaps_to_sentence_found_in_transcript():
    """Réponse réelle de Gemini du 24/10 (vidéo A-cipLy-c7s) : la citation commence à 8,7 s."""
    ans = {"sentences": [{"start": 0.0, "end": 8.0, "text": "Überraschende Kehrtwende."},
                         {"start": 8.7, "end": 17.5, "text": "Die Software Microsoft 365 wurde erst vor wenigen Monaten an Zehntausenden Arbeitsplätzen des Bundes installiert."}],
           "quote_start": 9, "quote_end": 50}
    h = H(9, 50)
    assert precise.refine_hit(L([ans]), "https://www.youtube.com/watch?v=A-cipLy-c7s", h, 246) == "ia-affiné"
    assert (h.t, h.t_end) == (8.7, 17.5)


def test_relative_times_are_converted_and_wider_window_tried():
    h = H(300, 310, "Switzerland wants to become more digitally independent today")
    first = {"sentences": [{"start": 1, "end": 4, "text": "something else entirely here"}], "quote_start": None, "quote_end": None}
    second = {"sentences": [{"start": 12.0, "end": 16.5, "text": "Switzerland wants to become more digitally independent today."}]}
    llm = L([first, second])
    assert precise.refine_hit(llm, "u", h, 1000) == "ia-affiné"
    assert llm.calls == [(275, 335), (210, 400)]   # fenêtre étroite puis large
    assert (h.t, h.t_end) == (222.0, 226.5)       # 210 + 12 : temps relatif au segment ramené au temps de la vidéo


def test_not_found_is_flagged_and_excluded_from_montage():
    h = H(506, 536, "Switzerland wants to become more digitally independent")
    nothing = {"sentences": [{"start": 486, "end": 495, "text": "I had Linux on my computer at university"}], "quote_start": None}
    assert precise.refine_hit(L([nothing, nothing]), "u", h, 900) == "non retrouvé"
    from quote_finder.models import VideoCandidate
    from quote_finder.pipeline import Options, VideoResult
    v = VideoCandidate("youtube", "HUn5SJMJ2fg", "https://www.youtube.com/watch?v=HUn5SJMJ2fg", "t", "", "swissinfo", "", 900)
    ok = H(100, 110)
    ok.timing = "ia-affiné"
    clips = report.montage_clips(Options("La Suisse", "x"), [VideoResult(v, [h, ok], "gemini-video")])
    assert [c["start"] for c in clips] == [100] and clips[0]["in"] == 98.8
