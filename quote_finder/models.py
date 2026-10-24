from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional

TARGET_TYPES = ("personne", "pays", "entite", "sujet")


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
    channel_id: str = ""
    channel_subs: Optional[int] = None  # abonnés de la chaîne (API YouTube)
    authority: float = 0.0              # 0..1, calculé par authority.score_video
    authority_why: str = ""
    relevance: float = 0.0              # 0..10, pertinence estimée avant analyse

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
    domain: str = ""        # domaine du média (même si l'URL est une redirection Google News)
    authority: float = 0.0
    authority_why: str = ""
    relevance: float = 0.0

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
    speaker_role: str = ""         # official | expert | politician | journalist | other
    timing: str = "ia"             # sous-titres (calé sur la transcription, ±1-2 s) | ia (estimé par l'IA, ±5 s)

    def to_dict(self):
        return asdict(self)


@dataclass
class Plan:
    claim_summary: str
    term_groups: list[list[str]]
    target_aliases: list[str]
    youtube_queries: list[str]
    news_queries: list[str]
    languages: list[str]
    source: str = "llm"  # llm | heuristic
    diag: dict = field(default_factory=dict)
    target_type: str = "personne"
    official_speakers: list[str] = field(default_factory=list)   # qui parle AU NOM de la cible (gouvernement, PDG...)
    authoritative_sources: list[str] = field(default_factory=list)  # médias / institutions de référence (noms ou domaines)

    def to_dict(self):
        return asdict(self)
