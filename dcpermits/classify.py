"""Data-center signal classifier.

Deciding whether a permit is data-center related is the part of this agent
that most affects output quality, and keyword grep alone performs badly in
both directions:

* **False positives.** Permit text is saturated with the word "data" in the
  low-voltage sense — "voice and data cabling", "data outlets", "data
  drops". A naive ``"data" in text`` match buries real signal. So does
  matching on scale alone: a $400M permit is just as likely a hospital or
  a stadium.
* **False negatives.** Plenty of genuine data-center work never says "data
  center". It says "Vadata Inc" in the owner field, or describes four
  2.5MW diesel generators and a chilled-water plant.

So classification is a weighted signal model in four groups:

``direct``
    Unambiguous topical terms ("data center", "data hall", "hyperscale").
``operator``
    Owner/applicant matches against the operator registry. Publicly
    reported shell entities score lower than direct corporate names,
    because shell attribution is inherently a lead rather than a fact.
``supporting``
    Mechanical/electrical tells that only mean something in combination —
    generators, switchgear, CRAH units, raised floor, megawatt ratings.
``scale``
    Valuation and square footage. These are *amplifiers only*: they add
    nothing unless a topical signal already fired.

Negative patterns can veto (for the data-cabling class, which is both very
common and unambiguous) or merely discount (residential context).

Every classification carries the list of signals that fired, so any number
in a report can be traced back to the specific words that produced it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .models import (
    ROLE_EQUIPMENT,
    ROLE_EXPANSION,
    ROLE_FITOUT,
    ROLE_NEW_BUILD,
    ROLE_OTHER,
    ROLE_POWER,
    TIER_CONFIRMED,
    TIER_POSSIBLE,
    TIER_PROBABLE,
    TIER_UNLIKELY,
    Permit,
)

DATA_DIR = Path(__file__).parent / "data"

# ---------------------------------------------------------------- signals

# Unambiguous. A single hit here is enough to make a permit interesting.
DIRECT_SIGNALS: Sequence[Tuple[str, str, float]] = (
    # An explicit, unambiguous mention is on its own enough to reach the
    # top tier — the phrase has no innocent reading in permit text once the
    # low-voltage "data cabling" family has been vetoed out.
    ("data_center", r"\bdata[\s\-_]?cent(?:er|re)s?\b", 60),
    ("datacenter_word", r"\bdatacent(?:er|re)s?\b", 60),
    ("data_hall", r"\bdata[\s\-]?halls?\b", 50),
    ("colocation", r"\bco[\s\-]?locations?\b|\bcolos?\s+(?:facility|center|site|building)\b", 45),
    ("hyperscale", r"\bhyper[\s\-]?scale\b", 45),
    ("server_farm", r"\bserver[\s\-]?farms?\b", 45),
    ("computer_room", r"\bcomputer\s+rooms?\b", 25),
    ("ai_campus", r"\b(?:ai|compute|cloud)\s+(?:campus|cluster|facility)\b", 35),
    ("cloud_region", r"\bcloud\s+(?:region|availability\s+zone)\b", 30),
)

# Meaningful in combination, weak alone.
SUPPORTING_SIGNALS: Sequence[Tuple[str, str, float]] = (
    ("megawatt", r"\b\d+(?:\.\d+)?\s*(?:mw|megawatts?)\b", 16),
    ("generators", r"\b(?:diesel\s+)?generators?\b|\bgensets?\b", 10),
    ("generator_bank", r"\b\d{1,2}\s+(?:diesel\s+)?generators?\b", 14),
    ("switchgear", r"\bswitch[\s\-]?gear\b", 12),
    ("substation", r"\b(?:electrical\s+)?sub[\s\-]?stations?\b", 12),
    ("ups_system", r"\buninterruptible\s+power\b|\bups\s+(?:system|unit|module)s?\b", 13),
    ("pdu", r"\b(?:pdus?|power\s+distribution\s+units?)\b", 12),
    ("crac_crah", r"\bcra[hc]\s?units?\b|\bcra[hc]\b", 14),
    ("chiller_plant", r"\bchill(?:er|ed)[\s\-]?(?:water\s+)?(?:plant|system|units?)\b", 10),
    ("cooling_tower", r"\bcooling\s+towers?\b", 8),
    ("raised_floor", r"\braised\s+(?:access\s+)?floors?\b", 11),
    ("white_space", r"\bwhite\s?space\b", 12),
    ("server_racks", r"\bserver\s+(?:racks?|cabinets?|rooms?)\b|\brack\s+(?:install|elevation)", 13),
    ("mission_critical", r"\bmission[\s\-]critical\b", 13),
    ("redundancy", r"\bn\s?\+\s?[12]\b|\b2n\s+redundan", 12),
    ("busway", r"\bbus[\s\-]?ways?\b|\bbus[\s\-]?ducts?\b", 8),
    ("fuel_storage", r"\b(?:diesel|fuel)\s+(?:oil\s+)?(?:storage\s+)?tanks?\b", 6),
    ("liquid_cooling", r"\b(?:liquid|immersion|direct[\s\-]to[\s\-]chip)\s+cooling\b", 15),
    ("transformer_bank", r"\btransformers?\b", 5),
    ("fiber", r"\bfiber\s+optic\b|\bdark\s+fiber\b", 5),
    ("technology_park", r"\btechnology\s+(?:park|campus)\b", 7),
)

# Vetoes. The low-voltage "data" family is the dominant false-positive
# class in real permit text and is safe to hard-exclude: these phrases
# describe network drops in ordinary buildings.
VETO_SIGNALS: Sequence[Tuple[str, str]] = (
    ("veto_data_cabling", r"\bdata\s+(?:cabl|wir|jack|outlet|port|drop|line|conduit)"),
    ("veto_voice_data", r"\bvoice\s*(?:,|/|and|&)?\s*data\b|\btele\s*/?\s*data\b"),
    ("veto_data_comm", r"\bdata\s*/\s*comm\b|\bcomm\s*/\s*data\b"),
    ("veto_low_voltage_data", r"\blow\s+voltage\s+data\b"),
    ("veto_call_center", r"\bcall\s+cent(?:er|re)s?\b"),
    # Telecom outside-plant work. This matters because carrier affiliates
    # carry hyperscaler names: "Google Fiber" permits for fiber huts and
    # vaults are a fiber-to-the-home network build, not a data center, and
    # they otherwise match the operator registry on every record.
    ("veto_fiber_network",
     r"\bfiber\s+(?:hut|network|vault|route|lateral|drop)\b"
     r"|\bgoogle\s+fiber\b|\bfiber\s+to\s+the\s+(?:home|premise)"
     r"|\bconduit\s+and\s+vaults?\b|\bhand\s?hole\b"),
    ("veto_wireless", r"\bsmall\s+cells?\b|\bcell\s+tower\b|\bmacro\s+site\b"
                      r"|\bantenna\s+(?:array|install)|\bmonopole\b|\bco\s?-?location\s+on\s+tower\b"),
)

# Discounts rather than vetoes: these contexts make a data-center reading
# unlikely but not impossible (a residential address can host a permit for
# an adjacent utility parcel).
DISCOUNT_SIGNALS: Sequence[Tuple[str, str, float]] = (
    ("residential", r"\bsingle[\s\-]family\b|\bduplex\b|\btownhom\w+\b|\bdwelling\b"
                    r"|\b(?:sfr|adu)\b|\bapartment\b|\bresidential\s+remodel\b", 30),
    ("reroof_fence", r"\bre[\s\-]?roof\b|\bfence\b|\bdriveway\b|\bswimming\s+pool\b"
                     r"|\bwater\s+heater\b|\bdeck\b", 25),
    ("sign_permit", r"\bsign\s+permit\b|\bwall\s+sign\b|\bmonument\s+sign\b", 20),
    ("demolition_only", r"\bdemolition\s+only\b|\bdemo\s+only\b", 10),
    # Buildings that declare what they are, and are not data centers.
    # Without this, large institutional projects score highly on supporting
    # signals alone: a hospital central plant has chillers, cooling towers,
    # emergency generators and switchgear, exactly like a data hall.
    ("other_building_type",
     r"\b(?:hospital|medical\s+(?:center|office|building)|clinic|surgery\s+center"
     r"|school|elementary|middle\s+school|high\s+school|university|dormitory"
     r"|hotel|motel|resort|casino|stadium|arena|ballpark|convention\s+center"
     r"|church|synagogue|mosque|temple|worship"
     r"|prison|jail|correctional|courthouse|fire\s+station|police\s+station"
     r"|grocery|supermarket|restaurant|retail\s+(?:store|center)|shopping\s+(?:mall|center)"
     r"|car\s+wash|gas\s+station|parking\s+(?:garage|structure)"
     r"|distribution\s+center|fulfillment\s+center|cold\s+storage"
     r"|water\s+treatment|waste\s?water|brewery|winery)\b", 35),
)

# ------------------------------------------------------------------- roles

ROLE_PATTERNS: Sequence[Tuple[str, str]] = (
    (ROLE_NEW_BUILD, r"\bnew\s+(?:construction|building|structure|commercial|facility|shell)\b"
                     r"|\bground[\s\-]?up\b|\bnew\s+data\b|\bcore\s+(?:and|&)\s+shell\b"
                     r"|\bshell\s+building\b|\bbuild(?:ing)?\s+shell\b"),
    (ROLE_EXPANSION, r"\bexpansion\b|\baddition\b|\bphase\s+(?:[2-9]|ii+|iv|v)\b"
                     r"|\bbuilding\s+[b-z]\b\s*$|\badditional\s+building\b"),
    (ROLE_POWER, r"\bsub[\s\-]?station\b|\bswitch[\s\-]?gear\b|\bgenerators?\b|\bgensets?\b"
                 r"|\belectrical\s+service\s+(?:upgrade|increase)\b|\btransformers?\b"
                 r"|\butility\s+(?:yard|upgrade)\b"),
    (ROLE_FITOUT, r"\btenant\s+(?:improvement|build[\s\-]?out|finish)\b|\bfit[\s\-]?out\b"
                  r"|\binterior\s+(?:remodel|alteration|renovation)\b|\bremodel\b"
                  r"|\balteration\b|\brenovation\b|\bwhite\s?space\s+build"),
    (ROLE_EQUIPMENT, r"\binstall\w*\s+(?:of\s+)?(?:equipment|racks?|units?)\b|\bequipment\s+"
                     r"(?:install|replacement|upgrade)\b|\breplace\w*\s+(?:rtu|hvac|unit)\b"
                     r"|\bmechanical\s+(?:only|permit)\b|\bracks?\b"),
)


@dataclass
class ClassifierConfig:
    """Tunable thresholds. Defaults chosen to favour precision."""

    confirmed_threshold: float = 60.0
    probable_threshold: float = 40.0
    possible_threshold: float = 20.0

    # Scale amplifiers: (minimum, points). Applied highest-first, once.
    valuation_tiers: Sequence[Tuple[float, float]] = (
        (250_000_000, 25.0),
        (100_000_000, 18.0),
        (50_000_000, 12.0),
        (10_000_000, 6.0),
    )
    sqft_tiers: Sequence[Tuple[float, float]] = (
        (500_000, 18.0),
        (200_000, 12.0),
        (100_000, 8.0),
        (50_000, 4.0),
    )
    # Shell-entity matches are leads, not facts, so they score below a
    # direct corporate-name match.
    operator_direct_points: float = 45.0
    operator_shell_points: float = 28.0
    # A lone supporting signal is noise; require this many before they
    # contribute their full weight.
    supporting_corroboration: int = 2


@dataclass
class Classification:
    score: float
    tier: str
    signals: List[str] = field(default_factory=list)
    operator: Optional[str] = None
    operator_match: Optional[str] = None
    role: str = ROLE_OTHER
    vetoed: bool = False

    @property
    def is_candidate(self) -> bool:
        """Whether this permit is worth surfacing at all."""
        return self.tier in (TIER_CONFIRMED, TIER_PROBABLE, TIER_POSSIBLE)


def _compile(signals: Sequence[Tuple[str, str, float]]):
    return [(name, re.compile(pattern, re.I), weight) for name, pattern, weight in signals]


class Classifier:
    def __init__(self, config: Optional[ClassifierConfig] = None,
                 operators_path: Optional[Path] = None):
        self.config = config or ClassifierConfig()
        self._direct = _compile(DIRECT_SIGNALS)
        self._supporting = _compile(SUPPORTING_SIGNALS)
        self._discount = _compile(DISCOUNT_SIGNALS)
        self._veto = [(n, re.compile(p, re.I)) for n, p in VETO_SIGNALS]
        self._roles = [(r, re.compile(p, re.I)) for r, p in ROLE_PATTERNS]
        self._operators = self._load_operators(operators_path)

    # ------------------------------------------------------------ operators

    @staticmethod
    def _load_operators(path: Optional[Path]) -> List[Dict]:
        path = path or (DATA_DIR / "operators.json")
        try:
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return []
        operators = []
        for entry in payload.get("operators", []):
            operators.append({
                "name": entry.get("name", "unknown"),
                "category": entry.get("category", "unknown"),
                # Longest-first so "amazon data services" wins over "aws".
                "aliases": sorted(
                    (a.lower() for a in entry.get("aliases", [])),
                    key=len, reverse=True,
                ),
                "shells": sorted(
                    (s.lower() for s in entry.get("shells", [])),
                    key=len, reverse=True,
                ),
            })
        return operators

    def match_operator(self, text: str) -> Tuple[Optional[str], Optional[str], float]:
        """Return (operator name, matched string, points)."""
        for operator in self._operators:
            for alias in operator["aliases"]:
                if self._contains_phrase(text, alias):
                    return operator["name"], alias, self.config.operator_direct_points
        for operator in self._operators:
            for shell in operator["shells"]:
                if self._contains_phrase(text, shell):
                    return operator["name"], shell, self.config.operator_shell_points
        return None, None, 0.0

    @staticmethod
    def _contains_phrase(text: str, phrase: str) -> bool:
        """Word-boundary containment, so "aws" does not match "lawsuit"."""
        return re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text) is not None

    # --------------------------------------------------------------- scoring

    def classify(self, permit: Permit) -> Classification:
        text = permit.text_blob()
        signals: List[str] = []

        vetoes = [name for name, pattern in self._veto if pattern.search(text)]
        direct_hits = [(name, weight) for name, pattern, weight in self._direct
                       if pattern.search(text)]

        # A veto only wins when nothing strongly topical fired. "Data
        # cabling for the new data center" is a data-center permit; "voice
        # and data cabling, 3rd floor office" is not.
        if vetoes and not direct_hits:
            return Classification(
                score=0.0, tier=TIER_UNLIKELY, signals=vetoes,
                role=self._role(text), vetoed=True,
            )

        score = 0.0
        for name, weight in direct_hits:
            # Only the strongest direct signal counts in full; further ones
            # add a little. Otherwise verbose permits inflate purely by
            # repeating synonyms.
            score += weight if not signals else weight * 0.15
            signals.append(name)

        operator, operator_match, operator_points = self.match_operator(text)
        if operator:
            score += operator_points
            signals.append(f"operator:{operator_match}")

        supporting_hits = [(name, weight) for name, pattern, weight in self._supporting
                           if pattern.search(text)]
        if supporting_hits:
            # Diminishing returns: sort strongest first, halve each step.
            supporting_hits.sort(key=lambda t: -t[1])
            enough = (len(supporting_hits) >= self.config.supporting_corroboration
                      or bool(direct_hits) or bool(operator))
            factor = 1.0 if enough else 0.35
            for index, (name, weight) in enumerate(supporting_hits):
                score += weight * factor * (0.5 ** index)
                signals.append(name)

        # Scale is an amplifier, never a trigger — otherwise every large
        # hospital and arena in the country lands in the results. It also
        # requires a *named* anchor (a direct term or an operator), not just
        # supporting MEP signals, because big mechanical/electrical scope is
        # exactly what large non-data-center projects also have.
        anchored = bool(direct_hits) or bool(operator)
        if anchored:
            for minimum, points in self.config.valuation_tiers:
                if permit.valuation and permit.valuation >= minimum:
                    score += points
                    signals.append(f"valuation>={int(minimum):,}".replace(",", "_"))
                    break
            for minimum, points in self.config.sqft_tiers:
                if permit.square_feet and permit.square_feet >= minimum:
                    score += points
                    signals.append(f"sqft>={int(minimum):,}".replace(",", "_"))
                    break

        for name, pattern, penalty in self._discount:
            if pattern.search(text):
                score -= penalty
                signals.append(f"-{name}")

        if vetoes:
            # Direct hit survived a veto, but the veto text is still a
            # reason for caution.
            score -= 10.0
            signals.extend(vetoes)

        score = max(0.0, round(score, 2))
        has_scale = any(s.startswith(("valuation>=", "sqft>=")) for s in signals)
        return Classification(
            score=score,
            tier=self._tier(score, bool(direct_hits), bool(operator),
                            len(supporting_hits), has_scale),
            signals=signals,
            operator=operator,
            operator_match=operator_match,
            role=self._role(text),
            vetoed=False,
        )

    def _tier(self, score: float, has_direct: bool, has_operator: bool,
              supporting_count: int, has_scale: bool) -> str:
        """Map a score to a confidence tier, gated on the *kind* of evidence.

        Score alone is not sufficient, because different evidence carries
        very different weight at the same number of points:

        * Only an explicit topical term justifies ``confirmed``. Supporting
          signals plus a large valuation can reach 60 points on a hospital
          central plant, and an operator name can reach it on an Apple
          retail store, neither of which is a data center.
        * An operator name on its own is a *lead* — hyperscalers own retail
          stores, offices and fiber plant. It needs corroboration from
          either a supporting signal or real scale before it counts as
          ``probable``.
        """
        cfg = self.config
        if has_direct and score >= cfg.confirmed_threshold:
            return TIER_CONFIRMED
        corroborated_operator = has_operator and (supporting_count >= 1 or has_scale)
        if score >= cfg.probable_threshold and (has_direct or corroborated_operator):
            return TIER_PROBABLE
        if score >= cfg.possible_threshold:
            return TIER_POSSIBLE
        return TIER_UNLIKELY

    def _role(self, text: str) -> str:
        # First pattern wins; ROLE_PATTERNS is ordered by how much a match
        # tells us about growth (a new build outranks an equipment swap).
        for role, pattern in self._roles:
            if pattern.search(text):
                return role
        return ROLE_OTHER

    # --------------------------------------------------------------- helpers

    def apply(self, permit: Permit) -> Permit:
        """Classify in place and return the permit."""
        result = self.classify(permit)
        permit.dc_score = result.score
        permit.dc_tier = result.tier
        permit.dc_signals = result.signals
        permit.dc_operator = result.operator
        permit.dc_role = result.role
        return permit

    def keywords(self) -> List[str]:
        """Plain keywords for server-side pre-filtering.

        Connectors push these into SoQL/ArcGIS ``where`` clauses so a
        nationwide sweep does not have to download every permit ever
        issued. Deliberately broader than the classifier: the goal is high
        recall, with precision applied locally afterwards.
        """
        terms = [
            "data center", "data centre", "datacenter", "data hall",
            "colocation", "hyperscale", "server farm", "computer room",
            "mission critical", "switchgear", "substation",
        ]
        for operator in self._operators:
            terms.extend(operator["aliases"][:2])
            terms.extend(operator["shells"])
        # Dedup, keep order stable for reproducible queries.
        seen, out = set(), []
        for term in terms:
            key = term.lower()
            if key not in seen:
                seen.add(key)
                out.append(key)
        return out
