"""Socrata (Tyler Open Data) connector.

Socrata powers open-data portals for a large share of US cities and
counties. It exposes SoQL, so keyword and date filtering push down to the
server — essential for a nationwide sweep, since the alternative is
downloading millions of irrelevant permits.

The connector degrades in stages rather than failing: if the server rejects
the keyword predicate it retries without it and filters locally, and if the
date predicate is rejected it drops that too. Portals run a wide range of
server versions and column types, so "this query should work" is not a safe
assumption.
"""

from __future__ import annotations

import logging
import urllib.parse
from typing import Any, Dict, Iterator, List, Optional, Sequence

from ..httpclient import HttpError
from ..models import SourceRef
from ..schema_map import find_address_parts, map_fields, pick_text_fields
from .base import Connector, HarvestQuery, build_text_predicate

log = logging.getLogger("dcpermits.connectors.socrata")

PAGE_SIZE = 1000
# Keywords are batched so the OR-ed predicate stays inside practical URL
# limits (Socrata rejects very long query strings).
KEYWORDS_PER_REQUEST = 8


class SocrataConnector(Connector):
    name = "socrata"

    # ------------------------------------------------------------- describe

    def describe(self, source: SourceRef) -> SourceRef:
        """Read column metadata and derive the field mapping."""
        columns = self._columns(source.endpoint)
        if columns:
            source.field_map = map_fields(columns)
            source.text_fields = pick_text_fields(columns)
            source.address_parts = find_address_parts(columns)
            source.date_field = (
                source.field_map.get("issued_date")
                or source.field_map.get("applied_date")
            )
            source.verified = bool(source.field_map.get("description")
                                   or source.field_map.get("permit_number"))
        return source

    def _columns(self, endpoint: str) -> List[Dict[str, Any]]:
        """Fetch column metadata for a dataset.

        Prefers the ``/api/views/{id}.json`` view, which reports declared
        types. Falls back to sampling a row when the view is restricted.
        """
        domain, dataset_id = _split_endpoint(endpoint)
        if domain and dataset_id:
            try:
                view = self.client.get_json(f"https://{domain}/api/views/{dataset_id}.json")
                columns = view.get("columns") or []
                normalized = [
                    {
                        "name": c.get("fieldName") or c.get("name"),
                        "type": c.get("dataTypeName"),
                    }
                    for c in columns
                    # Socrata prefixes internal system columns with ":".
                    if (c.get("fieldName") or "") and not str(c.get("fieldName")).startswith(":")
                ]
                if normalized:
                    return normalized
            except HttpError as exc:
                log.debug("view metadata unavailable for %s (%s); sampling", endpoint, exc)

        # Fallback: one row tells us the field names, though not the types.
        try:
            sample = self.client.get_json(endpoint, params={"$limit": 1})
        except HttpError as exc:
            log.warning("cannot introspect %s: %s", endpoint, exc)
            return []
        if isinstance(sample, list) and sample and isinstance(sample[0], dict):
            return [{"name": k, "type": None} for k in sample[0]
                    if not str(k).startswith(":")]
        return []

    # ---------------------------------------------------------------- fetch

    def fetch_rows(self, source: SourceRef, query: HarvestQuery) -> Iterator[Dict[str, Any]]:
        text_fields = list(source.text_fields or [])
        batches: List[Optional[Sequence[str]]]

        if query.full_scan or not text_fields or not query.keywords:
            # No usable text column, or an explicit full scan: pull the
            # window and filter locally.
            batches = [None]
        else:
            keywords = list(query.keywords)
            batches = [
                keywords[i:i + KEYWORDS_PER_REQUEST]
                for i in range(0, len(keywords), KEYWORDS_PER_REQUEST)
            ]

        seen_rows = 0
        emitted_ids = set()
        for batch in batches:
            if seen_rows >= query.limit:
                break
            for row in self._fetch_batch(source, query, batch, query.limit - seen_rows):
                # Batches overlap (a permit can match several keywords), so
                # dedup before handing rows upstream.
                identity = _row_identity(row)
                if identity in emitted_ids:
                    continue
                emitted_ids.add(identity)
                seen_rows += 1
                yield row
                if seen_rows >= query.limit:
                    break

    def _fetch_batch(self, source: SourceRef, query: HarvestQuery,
                     keywords: Optional[Sequence[str]], remaining: int
                     ) -> Iterator[Dict[str, Any]]:
        predicate = None
        if keywords:
            predicate = build_text_predicate(keywords, source.text_fields, "soql")

        date_clause = None
        if query.since and source.date_field:
            date_clause = f"{source.date_field} >= '{query.since}'"

        # Try the most selective query first, then relax on rejection.
        attempts = [(predicate, date_clause), (predicate, None), (None, None)]
        tried = set()

        for attempt_predicate, attempt_date in attempts:
            key = (attempt_predicate, attempt_date)
            if key in tried:
                continue
            tried.add(key)
            where = " AND ".join(
                f"({c})" for c in (attempt_predicate, attempt_date) if c
            )
            try:
                rows = list(self._paginate(source.endpoint, where, remaining))
            except HttpError as exc:
                if exc.status and 400 <= exc.status < 500:
                    log.debug("socrata rejected query on %s (%s); relaxing",
                              source.source_id, exc)
                    continue
                raise

            # When we had to drop the keyword predicate, apply it locally so
            # the caller still gets a filtered result.
            if attempt_predicate is None and query.keywords and not query.full_scan:
                yield from self._local_filter(rows, query.keywords, source.text_fields or None)
            else:
                yield from rows
            return

        log.warning("all query variants failed for %s", source.source_id)

    def _paginate(self, endpoint: str, where: str, remaining: int
                  ) -> Iterator[Dict[str, Any]]:
        offset = 0
        while remaining > 0:
            page = min(PAGE_SIZE, remaining)
            params: Dict[str, Any] = {"$limit": page, "$offset": offset}
            if where:
                params["$where"] = where
            # Stable ordering is required for correct pagination; Socrata
            # does not guarantee row order otherwise.
            params["$order"] = ":id"

            payload = self.client.get_json(endpoint, params=params)
            if not isinstance(payload, list):
                return
            for row in payload:
                if isinstance(row, dict):
                    yield row
            if len(payload) < page:
                return
            offset += len(payload)
            remaining -= len(payload)


def _split_endpoint(endpoint: str) -> tuple:
    """Extract (domain, dataset_id) from a Socrata resource URL."""
    parts = urllib.parse.urlsplit(endpoint)
    domain = parts.netloc or None
    dataset_id = None
    segments = [s for s in parts.path.split("/") if s]
    if segments:
        tail = segments[-1]
        for suffix in (".json", ".csv", ".geojson"):
            if tail.endswith(suffix):
                tail = tail[: -len(suffix)]
                break
        # Socrata 4x4 identifiers look like "ydr8-5enu".
        if len(tail) == 9 and tail[4] == "-":
            dataset_id = tail
    return domain, dataset_id


def resource_url(domain: str, dataset_id: str) -> str:
    return f"https://{domain}/resource/{dataset_id}.json"


def _row_identity(row: Dict[str, Any]) -> str:
    for key in (":id", "id", "permit_", "permit_number", "objectid"):
        if row.get(key) is not None:
            return f"{key}={row[key]}"
    return repr(sorted((k, str(v)) for k, v in row.items()))[:500]
