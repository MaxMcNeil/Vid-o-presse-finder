"""Comprend la recherche (cible = personne, pays, entité ou sujet) et prépare requêtes + termes à repérer."""
from __future__ import annotations

import logging
import time

from .models import TARGET_TYPES, Plan
from .util import as_dict, normalize

log = logging.getLogger("qf.planner")
PLAN_ATTEMPTS, PLAN_WAIT = 4, 25  # le plan est la base de tout : on insiste avant de renoncer

STOP = set("""dit dire disent disait says said say affirme affirment declare a an the of to in on at is are was were be been has have had that this these those and or for with
from by as it its he she they them his her their not no do does did can could would should will
le la les un une des du de d l et ou que qui quoi dans sur par pour avec est sont a ont au aux ce cet cette ces
il elle ils elles son sa ses leur leurs ne pas se
el los las una unos y o en por con es son lo del al
der die das ein eine und oder ist sind mit von zu im
il lo gli di che e è sono con per del della dei""".split())

TYPE_LABEL = {"personne": "une PERSONNE", "pays": "un PAYS (ou une ville / région)",
              "entite": "une ENTITÉ (entreprise, institution, parti, organisation...)", "sujet": "un SUJET / thème"}

PROMPT = """Tu prépares une recherche de déclarations dans des vidéos (transcriptions) et des articles de presse.
On veut PEU de résultats mais avec une AUTORITÉ FORTE : sources officielles, agences, grands médias, experts reconnus.

CIBLE : {target}
TYPE DE CIBLE : {type_line}
SUJET / AFFIRMATION RECHERCHÉ (mot, phrase, thème, affirmation... peut être vide) :
\"\"\"{claim}\"\"\"
QUI DOIT PARLER : {speakers_line}
AUTRES LOCUTEURS ACCEPTÉS : {others}
LANGUES SOUHAITÉES : {langs}

Réponds en JSON strict avec exactement ces clés :
{{
  "target_type": "personne" | "pays" | "entite" | "sujet",
  "claim_summary": "reformulation neutre et précise de ce qu'on cherche (1-2 phrases)",
  "term_groups": [["synonymes/formulations d'un concept clé, dans toutes les langues utiles, 4 à 12 variantes courtes, en minuscules, y compris les erreurs courantes de transcription automatique"], ...],
  "target_aliases": ["noms et variantes de la cible : nom complet, nom court, gentilé/adjectif (ex. suisse, swiss, helvétique), sigle, variantes de transcription"],
  "official_speakers": ["qui parle AU NOM de la cible, avec noms propres actuels : pour un pays le gouvernement, ministres, porte-parole, administrations concernées par le sujet ; pour une entité ses dirigeants et porte-parole ; pour une personne son entourage officiel (porte-parole) ; vide pour un sujet"],
  "authoritative_sources": ["10 à 20 sources de référence pour CE sujet : agences, médias publics et nationaux du pays concerné, institutions, sites officiels ; noms de chaînes YouTube ET domaines (ex. rts.ch, admin.ch, Reuters, franceinfo)"],
  "countries": ["codes ISO à 2 lettres des pays directement concernés (ex. CH pour la Suisse ; FR, US, SA, AE...), 1 à 6 ; pays du Golfe : GCC"],
  "site_query": "4 à 7 mots-clés courts pour chercher ce sujet sur le site d'un média, dans la langue du pays concerné (ex. Confédération Microsoft 365 souveraineté)",
  "youtube_queries": ["6 à 10 requêtes visant des sources sérieuses : journal télévisé, conférence de presse, déclaration officielle, interview, débat parlementaire, nom de média + sujet ; langues variées"],
  "news_queries": ["4 à 6 requêtes pour articles d'agences et grands médias"]
}}
Règles : 2 à 4 groupes dans term_groups, chaque groupe = UN concept du SUJET (pas la cible elle-même, sauf si la cible est un SUJET :
alors 1 à 3 groupes décrivant le sujet). Si le sujet est vide, déduis les concepts de la cible. Pas de requêtes racoleuses.
Langues : mets les variantes de term_groups et les requêtes dans CHAQUE langue souhaitée ET dans la langue des pays concernés
(ex. italien pour l'Italie, arabe pour les pays du Golfe). En arabe, donne les formes avec et sans l'article « ال »."""


def _heuristic(target: str, claim: str, others: list[str], langs: list[str], ttype: str) -> Plan:
    words = [w for w in normalize(claim or target).split() if w not in STOP and len(w) > 2]
    groups = [[w] for w in dict.fromkeys(words)][:4] or [[normalize(claim or target)]]
    q = f"{target} {claim}".strip()
    yt = [q, f"{q} interview", f"{q} conférence de presse", f"{q} journal"]
    return Plan(claim or target, groups, [normalize(n) for n in [target] + others if n], yt,
                [f'"{target}" {claim}'.strip(), q], langs, source="heuristic", target_type=ttype if ttype in TARGET_TYPES else "sujet")


def make_plan(llm, target: str, claim: str, others: list[str], langs: list[str], speakers: str = "tous",
              target_type: str = "auto") -> Plan:
    if llm is None or not llm.has_text:
        log.warning("Pas de LLM : plan heuristique (moins précis)")
        return _heuristic(target, claim, others, langs, target_type)
    type_line = TYPE_LABEL.get(target_type, "à déterminer (personne, pays, entité ou sujet)")
    anyone = ("n'importe qui, à condition d'être une voix qui fait autorité (officiel, expert, grand média) ; "
              "la cible est le SUJET des propos")
    speakers_line = {"cible": "la CIBLE elle-même (ou, pour un pays/une entité, ses représentants officiels)",
                     "tous": anyone}.get(speakers, "si la cible est une personne : ses propres propos ; sinon : " + anyone)
    last = None
    for attempt in range(PLAN_ATTEMPTS):
        try:
            d = llm.generate_json(PROMPT.format(target=target, type_line=type_line, claim=claim or "(aucun : tout ce qui est "
                                                "marquant et récent à propos de la cible)", speakers_line=speakers_line,
                                                others=", ".join(others) or "aucun", langs=", ".join(langs)))
            d = as_dict(d)
            groups = [[normalize(t) for t in (g if isinstance(g, list) else [g]) if isinstance(t, str) and normalize(t)]
                      for g in d.get("term_groups", []) if g]
            groups = [g for g in groups if g][:4]
            if not groups:
                raise ValueError("term_groups vide")
            ttype = target_type if target_type in TARGET_TYPES else str(d.get("target_type", "")).lower().replace("é", "e")
            if ttype not in TARGET_TYPES:
                ttype = "sujet"
            aliases = [normalize(a) for a in (d.get("target_aliases") or d.get("person_aliases") or []) if a] + \
                      [normalize(target)] + [normalize(o) for o in others]
            base_q = f"{target} {claim}".strip()
            return Plan(
                claim_summary=str(d.get("claim_summary") or claim or target),
                term_groups=groups,
                target_aliases=list(dict.fromkeys(a for a in aliases if a)),
                youtube_queries=list(dict.fromkeys([base_q] + [q for q in d.get("youtube_queries", []) if q]))[:10],
                news_queries=list(dict.fromkeys([base_q] + [q for q in d.get("news_queries", []) if q]))[:6],
                languages=langs,
                target_type=ttype,
                official_speakers=[str(s) for s in (d.get("official_speakers") or []) if s][:15],
                authoritative_sources=[str(s) for s in (d.get("authoritative_sources") or []) if s][:25],
                countries=[str(c).upper()[:2] for c in (d.get("countries") or []) if c][:3],
                site_query=" ".join(str(d.get("site_query") or "").split()[:8]),
            )
        except Exception as e:  # noqa: BLE001
            last = e
            log.warning("Planification LLM échouée (essai %d/%d) : %s", attempt + 1, PLAN_ATTEMPTS, str(e)[:150])
            if attempt + 1 < PLAN_ATTEMPTS:
                time.sleep(PLAN_WAIT)
    log.error("Planification impossible après %d essais (%s) : plan heuristique", PLAN_ATTEMPTS, str(last)[:100])
    return _heuristic(target, claim, others, langs, target_type)
