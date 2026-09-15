"""Connector contract shared by every source type."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence

from ..httpclient import PoliteClient
from ..models import Permit, SourceRef
from ..normalize import (
    clean_text,
    normalize_zip,
    parse_coord,
    parse_date,
    parse_money,
    parse_number,
    utcnow_iso,
)

log = logging.getLogger("dcpermits.connectors")

# How each canonical field is coerced out of a raw provider row.
_PARSERS = {
    "valuation": parse_money,
    "square_feet": parse_number,
    "applied_date": parse_date,
    "issued_date": parse_date,
    "final_date": parse_date,
    "zipcode": normalize_zip,
    "latitude": lambda v: parse_coord(v, "lat"),
    "longitude": lambda v: parse_coord(v, "lon"),
}


@dataclass
class HarvestQuery:
    """What to ask a source for."""

    keywords: Sequence[str] = ()
    since: Optional[str] = None       # ISO date; filters on the source date field
    limit: int = 5000
    # When True, skip server-side keyword filtering and pull everything in
    # the window, filtering locally. Needed for sources with no usable text
    # column, and useful for auditing what server-side filtering misses.
    full_scan: bool = False


class Connector:
    """Base class. Subclasses implement :meth:`fetch_rows` and :meth:`describe`."""

    name = "base"

    def __init__(self, client: PoliteClient):
        self.client = client

    # ------------------------------------------------------------- interface

    def describe(self, source: SourceRef) -> SourceRef:
        """Introspect the endpoint and populate field_map/text_fields."""
        raise NotImplementedError

    def fetch_rows(self, source: SourceRef, query: HarvestQuery) -> Iterator[Dict[str, Any]]:
        """Yield raw provider rows."""
        raise NotImplementedError

    # -------------------------------------------------------------- harvest

    def harvest(self, source: SourceRef, query: HarvestQuery) -> Iterator[Permit]:
        """Fetch rows and convert them to canonical permits."""
        for row in self.fetch_rows(source, query):
            permit = self.to_permit(source, row)
            if permit is not None:
                yield permit

    def to_permit(self, source: SourceRef, row: Dict[str, Any]) -> Optional[Permit]:
        """Apply the field map and normalize values."""
        field_map = source.field_map or {}
        values: Dict[str, Any] = {}
        for canonical, provider_field in field_map.items():
            raw = row.get(provider_field)
            if raw is None:
                continue
            parser = _PARSERS.get(canonical, clean_text)
            parsed = parser(raw)
            if parsed is not None:
                values[canonical] = parsed

        # Some feeds split the address across columns instead of providing
        # one; assemble it when no single address field was mapped.
        if not values.get("address") and source.address_parts:
            assembled = self._assemble_address(row, source.address_parts)
            if assembled:
                values["address"] = assembled

        # A row with no text and no identity is not worth storing.
        if not any(values.get(k) for k in
                   ("description", "permit_number", "permit_type", "address", "owner")):
            return None

        return Permit(
            source_id=source.source_id,
            jurisdiction=source.jurisdiction,
            state=source.state,
            fetched_at=utcnow_iso(),
            raw=self._trim_raw(row),
            **values,
        )

    @staticmethod
    def _assemble_address(row: Dict[str, Any], parts: Sequence[str]) -> Optional[str]:
        pieces = []
        for part in parts:
            value = clean_text(row.get(part))
            if value:
                pieces.append(value)
        return " ".join(pieces) if pieces else None

    @staticmethod
    def _trim_raw(row: Dict[str, Any], max_chars: int = 4000) -> Dict[str, Any]:
        """Keep provenance without letting geometry blobs bloat the store."""
        trimmed: Dict[str, Any] = {}
        budget = max_chars
        for key, value in row.items():
            if key in ("geometry", "shape", "SHAPE", "the_geom"):
                continue
            text = value if isinstance(value, (str, int, float, bool, type(None))) else str(value)
            size = len(str(text))
            if size > budget:
                continue
            trimmed[key] = text
            budget -= size
        return trimmed

    # --------------------------------------------------------------- helpers

    @staticmethod
    def _local_filter(rows: Iterable[Dict[str, Any]], keywords: Sequence[str],
                      fields: Optional[Sequence[str]] = None) -> Iterator[Dict[str, Any]]:
        """Client-side keyword filter for sources that cannot filter server-side."""
        if not keywords:
            yield from rows
            return
        lowered = [k.lower() for k in keywords]
        for row in rows:
            haystack = " ".join(
                str(v).lower()
                for k, v in row.items()
                if v is not None and (fields is None or k in fields)
            )
            if any(term in haystack for term in lowered):
                yield row


def build_text_predicate(keywords: Sequence[str], fields: Sequence[str],
                         dialect: str) -> Optional[str]:
    """Build an OR-ed case-insensitive LIKE predicate.

    ``dialect`` is ``"soql"`` (Socrata) or ``"sql92"`` (ArcGIS). Both
    support ``UPPER(field) LIKE '%TERM%'``, which is the most portable
    option across the wide range of server versions in the wild.

    Returns ``None`` when there is nothing to filter on, which tells the
    caller to fall back to a full scan plus local filtering.
    """
    if not keywords or not fields:
        return None

    clauses: List[str] = []
    for field_name in fields:
        quoted_field = _quote_identifier(field_name, dialect)
        for keyword in keywords:
            escaped = keyword.upper().replace("'", "''").replace("%", "")
            if not escaped.strip():
                continue
            clauses.append(f"UPPER({quoted_field}) LIKE '%{escaped}%'")
    if not clauses:
        return None
    return " OR ".join(clauses)


def _quote_identifier(name: str, dialect: str) -> str:
    # ArcGIS/SQL-92 tolerates bare identifiers and most permit layers use
    # safe names. Socrata field names are already lowercased API names.
    if dialect == "soql":
        return name
    return name
