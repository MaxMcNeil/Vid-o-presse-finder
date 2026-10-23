"""Comprend la recherche (mot, phrase, affirmation...) et prépare requêtes + termes à repérer."""
from __future__ import annotations

import logging
import re

from .models import Plan
from .util import normalize

log = logging.getLogger("qf.planner")

STOP = set("""dit dire disent disait says said say affirme affirment declare a an the of to in on at is are was were be been has have had that this these those and or for with
from by as it its he she they them his her their not no do does did can could would should will
le la les un une des du de d l et ou que qui quoi dans sur par pour avec est sont a ont au aux ce cet cette ces
il elle ils elles son sa ses leur leurs ne pas se
el los las una unos y o en por con es son lo del al
der die das ein eine und oder ist sind mit von zu im
il lo gli di che e è sono con per del della dei""".split())

PROMPT = """Tu prépares une recherche de citations dans des vidéos (transcriptions) et des articles de presse.

PERSONNE CIBLE : {person}
AUTRES LOCUTEURS ACCEPTÉS : {others}
{anyone_line}AFFIRMATION / SUJET RECHERCHÉ (peut être un mot, une phrase, un thème, un pays, une affirmation...) :
\"\"\"{claim}\"\"\"
LANGUES SOUHAITÉES POUR LES REQUÊTES : {langs}

Réponds en JSON strict avec exactement ces clés :
{{
  "claim_summary": "reformulation neutre et précise de ce qu'on cherche (1-2 phrases)",
  "term_groups": [["synonymes/formulations d'un concept clé, dans toutes les langues utiles, 4 à 12 variantes courtes, en minuscules, y compris les erreurs courantes de transcription automatique"], ...],
  "person_aliases": ["nom complet", "nom court", "titre + nom", "variantes de transcription"],
  "youtube_queries": ["8 à 14 requêtes de recherche variées : discours, interview, conférence de presse, extrait, avec/sans année, langues différentes"],
  "news_queries": ["4 à 8 requêtes pour articles/agences : citations entre guillemets, noms d'agences"]
}}
Règles : 2 à 5 groupes dans term_groups, chaque groupe = UN concept (ex. l'objet, l'action, le sujet). Ne mets pas le nom de la personne dans term_groups. Si l'affirmation est un simple mot, un seul groupe avec ses variantes et traductions suffit."""


def _heuristic(person: str, claim: str, others: list[str], langs: list[str]) -> Plan:
    words = [w for w in normalize(claim).split() if w not in STOP and len(w) > 2]
    groups = [[w] for w in dict.fromkeys(words)][:5] or [[normalize(claim)]]
    names = [person] + others
    yt = [f"{person} {claim}", f"{person} {claim} interview", f"{person} {claim} speech",
          f"{person} says {claim}", f'"{claim}" {person}']
    return Plan(claim, groups, [normalize(n) for n in names if n], yt,
                [f'"{person}" {claim}', f"{person} {claim}"], langs)


def make_plan(llm, person: str, claim: str, others: list[str], langs: list[str], anyone: bool = False) -> Plan:
    if llm is None or not llm.has_text:
        log.warning("Pas de LLM : plan heuristique (moins précis)")
        return _heuristic(person, claim, others, langs)
    try:
        d = llm.generate_json(PROMPT.format(person=person, others=", ".join(others) or "aucun", claim=claim,
                                            langs=", ".join(langs),
                                            anyone_line=("TOUT LOCUTEUR EST ACCEPTÉ : la personne cible est le SUJET de l'affirmation, "
                                                         "pas forcément celui qui parle (ex. des gens qui disent que cette personne ment).\n") if anyone else ""))
        groups = [[normalize(t) for t in g if normalize(t)] for g in d.get("term_groups", []) if g]
        groups = [g for g in groups if g][:5]
        if not groups:
            raise ValueError("term_groups vide")
        aliases = [normalize(a) for a in d.get("person_aliases", []) if a] + [normalize(person)] + \
                  [normalize(o) for o in others]
        return Plan(
            claim_summary=str(d.get("claim_summary") or claim),
            term_groups=groups,
            person_aliases=list(dict.fromkeys(a for a in aliases if a)),
            youtube_queries=list(dict.fromkeys([f"{person} {claim}"] + [q for q in d.get("youtube_queries", []) if q]))[:16],
            news_queries=list(dict.fromkeys([f'"{person}" {claim}'] + [q for q in d.get("news_queries", []) if q]))[:10],
            languages=langs,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("Planification LLM échouée (%s) : plan heuristique", e)
        return _heuristic(person, claim, others, langs)
