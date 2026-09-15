"""Generic CSV / JSON-array connector.

The long tail of jurisdictions publishes a flat CSV or JSON export on a
plain web server with no query API at all. There is nothing to push a
filter into, so these are always full-scan-and-filter-locally. Useful for
pinning a specific known-good export that neither Socrata nor ArcGIS
discovery covers.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from typing import Any, Dict, Iterator, List

from ..httpclient import HttpError
from ..models import SourceRef
from ..schema_map import find_address_parts, map_fields, pick_text_fields
from .base import Connector, HarvestQuery

log = logging.getLogger("dcpermits.connectors.csvfeed")


class CsvConnector(Connector):
    name = "csv"

    def describe(self, source: SourceRef) -> SourceRef:
        try:
            rows = list(self._read(source.endpoint, limit=5))
        except HttpError as exc:
            log.warning("cannot introspect %s: %s", source.endpoint, exc)
            return source
        if not rows:
            return source
        names = list(rows[0].keys())
        source.field_map = map_fields(names)
        source.text_fields = pick_text_fields(names)
        source.address_parts = find_address_parts(names)
        source.date_field = (source.field_map.get("issued_date")
                             or source.field_map.get("applied_date"))
        source.verified = bool(source.field_map.get("description")
                               or source.field_map.get("permit_number"))
        return source

    def fetch_rows(self, source: SourceRef, query: HarvestQuery) -> Iterator[Dict[str, Any]]:
        rows = self._read(source.endpoint, limit=None)
        if query.keywords and not query.full_scan:
            rows = self._local_filter(rows, query.keywords, source.text_fields or None)
        for index, row in enumerate(rows):
            if index >= query.limit:
                return
            yield row

    def _read(self, endpoint: str, limit) -> Iterator[Dict[str, Any]]:
        body = self.client.get_text(endpoint)
        stripped = body.lstrip()
        if stripped.startswith("[") or stripped.startswith("{"):
            yield from self._read_json(stripped, limit)
        else:
            yield from self._read_csv(body, limit)

    @staticmethod
    def _read_json(body: str, limit) -> Iterator[Dict[str, Any]]:
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise HttpError(f"invalid JSON feed: {exc}") from exc
        if isinstance(payload, dict):
            # Find the first list-of-dicts value; feeds wrap rows under
            # keys like "results", "data" or "features".
            for value in payload.values():
                if isinstance(value, list) and value and isinstance(value[0], dict):
                    payload = value
                    break
            else:
                return
        if not isinstance(payload, list):
            return
        for index, row in enumerate(payload):
            if limit is not None and index >= limit:
                return
            if isinstance(row, dict):
                yield row

    @staticmethod
    def _read_csv(body: str, limit) -> Iterator[Dict[str, Any]]:
        try:
            dialect = csv.Sniffer().sniff(body[:8192], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(io.StringIO(body), dialect=dialect)
        for index, row in enumerate(reader):
            if limit is not None and index >= limit:
                return
            yield {k: v for k, v in row.items() if k}


def sniff_columns(body: str) -> List[str]:
    """Column names from a CSV body, for tests and diagnostics."""
    reader = csv.reader(io.StringIO(body))
    for header in reader:
        return [h.strip() for h in header if h.strip()]
    return []
