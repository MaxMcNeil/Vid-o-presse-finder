from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Segment:
    start: float  # secondes
    text: str


@dataclass
class Transcript:
    segments: list[Segment]
    lang: str = "?"
    kind: str = "unknown"  # manual | auto | subtitles


@dataclass
class VideoCandidate:
    platform: str  # youtube | dailymotion | archive
    id: str
    url: str
    title: str = ""
    description: str = ""
    channel: str = ""
    published: str = ""  # ISO 8601 ou YYYY-MM-DD
    duration: Optional[int] = None
    found_by: set = field(default_factory=set)

    @property
    def key(self) -> str:
        return f"{self.platform}:{self.id}"


@dataclass
class ArticleCandidate:
    source: str
    url: str
    title: str = ""
    published: str = ""
    snippet: str = ""
    text: str = ""
    found_by: set = field(default_factory=set)

    @property
    def key(self) -> str:
        return self.url


@dataclass
class Hit:
    t: Optional[float]  # début, en secondes (None pour un article)
    quote: str
    verdict: str  # literal | paraphrase | mention | denial
    who_says: str  # target | other_named | reported | unknown
    speaker_name: str = ""
    confidence: float = 0.0
    why: str = ""
    t_end: Optional[float] = None  # fin de la déclaration, en secondes

    def to_dict(self):
        return asdict(self)


@dataclass
class Plan:
    claim_summary: str
    term_groups: list[list[str]]
    person_aliases: list[str]
    youtube_queries: list[str]
    news_queries: list[str]
    languages: list[str]
    source: str = "llm"  # llm | heuristic
    diag: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)
