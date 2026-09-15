"""Canonical data model.

Every jurisdiction publishes permits with its own field names, types and
conventions. Everything downstream of the connectors speaks the canonical
:class:`Permit` instead, so the classifier and reports never have to care
where a record came from.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

# Confidence tiers assigned by the classifier.
TIER_CONFIRMED = "confirmed"
TIER_PROBABLE = "probable"
TIER_POSSIBLE = "possible"
TIER_UNLIKELY = "unlikely"

TIER_ORDER = [TIER_CONFIRMED, TIER_PROBABLE, TIER_POSSIBLE, TIER_UNLIKELY]

# How a permit relates to data-center growth. Distinguishing a ground-up
# campus from a rack-level fit-out matters a lot when measuring growth: a
# single market can log hundreds of small electrical permits against one
# existing building.
ROLE_NEW_BUILD = "new_build"
ROLE_EXPANSION = "expansion"
ROLE_FITOUT = "fitout"
ROLE_POWER = "power_infrastructure"
ROLE_EQUIPMENT = "equipment"
ROLE_OTHER = "other"


@dataclass
class SourceRef:
    """A validated, queryable permit endpoint."""

    source_id: str
    connector: str               # "socrata" | "arcgis" | "csv"
    endpoint: str
    jurisdiction: str
    state: Optional[str] = None
    # Field mapping from canonical name -> provider field name. Populated by
    # discovery/introspection, or pinned by hand in the seed registry.
    field_map: Dict[str, str] = field(default_factory=dict)
    # Provider field the connector can push a keyword filter into. When this
    # is None the connector harvests broadly and filters client-side.
    text_fields: List[str] = field(default_factory=list)
    # Provider fields to join into a street address when the source has no
    # single address column (e.g. street_number + street_name).
    address_parts: List[str] = field(default_factory=list)
    date_field: Optional[str] = None
    notes: Optional[str] = None
    record_count: Optional[int] = None
    verified: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SourceRef":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass
class Permit:
    """One permit, normalized."""

    source_id: str
    jurisdiction: str
    state: Optional[str] = None

    permit_number: Optional[str] = None
    permit_type: Optional[str] = None
    work_class: Optional[str] = None
    status: Optional[str] = None
    description: Optional[str] = None

    address: Optional[str] = None
    city: Optional[str] = None
    zipcode: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    applicant: Optional[str] = None
    owner: Optional[str] = None
    contractor: Optional[str] = None

    valuation: Optional[float] = None
    square_feet: Optional[float] = None

    applied_date: Optional[str] = None      # ISO-8601 date
    issued_date: Optional[str] = None
    final_date: Optional[str] = None

    url: Optional[str] = None
    fetched_at: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    # Filled in by the classifier.
    dc_score: float = 0.0
    dc_tier: str = TIER_UNLIKELY
    dc_signals: List[str] = field(default_factory=list)
    dc_operator: Optional[str] = None
    dc_role: str = ROLE_OTHER

    @property
    def uid(self) -> str:
        """Stable identity for dedup across re-runs and overlapping sources.

        Keyed on source + permit number when available, since the same permit
        can be republished with a shifting internal row id. Falls back to a
        content hash for sources that expose no permit number at all.
        """
        if self.permit_number:
            basis = f"{self.source_id}|{self.permit_number}".lower()
        else:
            basis = "|".join(
                str(x or "")
                for x in (
                    self.source_id,
                    self.address,
                    self.issued_date or self.applied_date,
                    (self.description or "")[:120],
                )
            ).lower()
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]

    @property
    def best_date(self) -> Optional[str]:
        """The date to attribute this permit to for trend analysis."""
        return self.issued_date or self.applied_date or self.final_date

    def text_blob(self) -> str:
        """All free text the classifier should consider, lowercased."""
        parts = [
            self.description,
            self.permit_type,
            self.work_class,
            self.applicant,
            self.owner,
            self.contractor,
            self.address,
        ]
        return " \n".join(p for p in parts if p).lower()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)


# Canonical field names a connector may map into. Kept in one place so the
# schema mapper, the store and the CSV exporter cannot drift apart.
CANONICAL_FIELDS = [
    "permit_number",
    "permit_type",
    "work_class",
    "status",
    "description",
    "address",
    "city",
    "zipcode",
    "latitude",
    "longitude",
    "applicant",
    "owner",
    "contractor",
    "valuation",
    "square_feet",
    "applied_date",
    "issued_date",
    "final_date",
]
