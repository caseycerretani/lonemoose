"""ArcGIS FeatureServer / MapServer connector.

ArcGIS is the other half of the US permit landscape — counties that never
adopted an open-data portal still publish permits as a hosted feature
layer. Discovery through ArcGIS Hub surfaces roughly ten thousand
permit-ish layers nationwide.

Two realities shape this connector:

* **Field names are arbitrary.** A layer's description column may be
  ``DESCRIPTION``, ``PROJ_DESC`` or ``Field14``. Querying a field that does
  not exist returns HTTP 400, so the connector introspects the layer first
  and only ever references fields it has seen.
* **Capabilities vary.** Older servers lack ``supportsPagination``, so the
  connector falls back to paging by object ID, which every version
  supports.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterator, List, Optional, Sequence

from ..httpclient import HttpError
from ..models import SourceRef
from ..schema_map import find_address_parts, map_fields, pick_text_fields
from .base import Connector, HarvestQuery, build_text_predicate

log = logging.getLogger("dcpermits.connectors.arcgis")

KEYWORDS_PER_REQUEST = 6
DEFAULT_PAGE = 1000


class ArcGISConnector(Connector):
    name = "arcgis"

    # ------------------------------------------------------------- describe

    def describe(self, source: SourceRef) -> SourceRef:
        meta = self._layer_meta(source.endpoint)
        if not meta:
            return source

        fields = [f for f in meta.get("fields", []) if f.get("name")]
        source.field_map = map_fields(fields)
        source.text_fields = pick_text_fields(fields)
        source.address_parts = find_address_parts(fields)
        source.date_field = (
            source.field_map.get("issued_date") or source.field_map.get("applied_date")
        )
        source.notes = meta.get("name") or source.notes
        source.verified = bool(source.field_map.get("description")
                               or source.field_map.get("permit_number"))

        # Cache capabilities the fetch path needs.
        self._capabilities[source.endpoint] = {
            "max_record_count": meta.get("maxRecordCount") or DEFAULT_PAGE,
            "supports_pagination": bool(
                meta.get("advancedQueryCapabilities", {}).get("supportsPagination")
            ),
            "oid_field": meta.get("objectIdField") or _find_oid_field(fields),
        }

        try:
            source.record_count = self._count(source.endpoint, "1=1")
        except HttpError:
            source.record_count = None
        return source

    def __init__(self, client):
        super().__init__(client)
        self._capabilities: Dict[str, Dict[str, Any]] = {}

    def _layer_meta(self, endpoint: str) -> Optional[Dict[str, Any]]:
        try:
            meta = self.client.get_json(endpoint, params={"f": "json"})
        except HttpError as exc:
            log.warning("cannot introspect %s: %s", endpoint, exc)
            return None
        if not isinstance(meta, dict) or "error" in meta:
            log.debug("layer metadata error for %s: %s", endpoint,
                      (meta or {}).get("error") if isinstance(meta, dict) else meta)
            return None
        if not meta.get("fields"):
            return None
        return meta

    def _count(self, endpoint: str, where: str) -> Optional[int]:
        payload = self.client.get_json(
            f"{endpoint.rstrip('/')}/query",
            params={"where": where, "returnCountOnly": "true", "f": "json"},
        )
        if isinstance(payload, dict):
            return payload.get("count")
        return None

    # ---------------------------------------------------------------- fetch

    def fetch_rows(self, source: SourceRef, query: HarvestQuery) -> Iterator[Dict[str, Any]]:
        if source.endpoint not in self._capabilities:
            # Harvest may be called on a source loaded from the registry
            # without a fresh describe(); introspect lazily.
            self.describe(source)

        text_fields = list(source.text_fields or [])
        if query.full_scan or not text_fields or not query.keywords:
            batches: List[Optional[Sequence[str]]] = [None]
        else:
            keywords = list(query.keywords)
            batches = [
                keywords[i:i + KEYWORDS_PER_REQUEST]
                for i in range(0, len(keywords), KEYWORDS_PER_REQUEST)
            ]

        emitted = 0
        seen = set()
        for batch in batches:
            if emitted >= query.limit:
                break
            for row in self._fetch_batch(source, query, batch, query.limit - emitted):
                identity = _row_identity(row)
                if identity in seen:
                    continue
                seen.add(identity)
                emitted += 1
                yield row
                if emitted >= query.limit:
                    break

    def _fetch_batch(self, source: SourceRef, query: HarvestQuery,
                     keywords: Optional[Sequence[str]], remaining: int
                     ) -> Iterator[Dict[str, Any]]:
        predicate = None
        if keywords:
            predicate = build_text_predicate(keywords, source.text_fields, "sql92")

        date_clause = None
        if query.since and source.date_field:
            # Hosted feature services accept the TIMESTAMP literal form
            # more consistently than a bare date string.
            date_clause = f"{source.date_field} >= TIMESTAMP '{query.since} 00:00:00'"

        attempts = [(predicate, date_clause), (predicate, None), (None, None)]
        tried = set()
        for attempt_predicate, attempt_date in attempts:
            key = (attempt_predicate, attempt_date)
            if key in tried:
                continue
            tried.add(key)
            where = " AND ".join(f"({c})" for c in (attempt_predicate, attempt_date) if c) or "1=1"
            try:
                rows = list(self._paginate(source, where, remaining))
            except HttpError as exc:
                if exc.status and 400 <= exc.status < 500:
                    log.debug("arcgis rejected query on %s (%s); relaxing",
                              source.source_id, exc)
                    continue
                raise

            if attempt_predicate is None and query.keywords and not query.full_scan:
                yield from self._local_filter(rows, query.keywords, source.text_fields or None)
            else:
                yield from rows
            return

        log.warning("all query variants failed for %s", source.source_id)

    def _paginate(self, source: SourceRef, where: str, remaining: int
                  ) -> Iterator[Dict[str, Any]]:
        caps = self._capabilities.get(source.endpoint, {})
        page_size = min(int(caps.get("max_record_count") or DEFAULT_PAGE), DEFAULT_PAGE)
        query_url = f"{source.endpoint.rstrip('/')}/query"

        if caps.get("supports_pagination"):
            offset = 0
            while remaining > 0:
                params = {
                    "where": where,
                    "outFields": "*",
                    "returnGeometry": "false",
                    "resultOffset": offset,
                    "resultRecordCount": min(page_size, remaining),
                    "f": "json",
                }
                features = self._features(query_url, params)
                if features is None:
                    return
                for feature in features:
                    yield feature
                if len(features) < min(page_size, remaining):
                    return
                offset += len(features)
                remaining -= len(features)
            return

        # No pagination support: walk forward by object ID, which works on
        # every server version.
        oid_field = caps.get("oid_field") or "OBJECTID"
        last_oid = -1
        while remaining > 0:
            paged_where = f"({where}) AND {oid_field} > {last_oid}"
            params = {
                "where": paged_where,
                "outFields": "*",
                "returnGeometry": "false",
                "orderByFields": f"{oid_field} ASC",
                "resultRecordCount": min(page_size, remaining),
                "f": "json",
            }
            features = self._features(query_url, params)
            if not features:
                return
            max_oid = last_oid
            for feature in features:
                yield feature
                oid = feature.get(oid_field)
                if isinstance(oid, (int, float)):
                    max_oid = max(max_oid, int(oid))
            if max_oid <= last_oid:
                # Cannot advance — bail rather than loop forever.
                return
            last_oid = max_oid
            remaining -= len(features)

    def _features(self, query_url: str, params: Dict[str, Any]
                  ) -> Optional[List[Dict[str, Any]]]:
        payload = self.client.get_json(query_url, params=params)
        if not isinstance(payload, dict):
            return None
        if "error" in payload:
            # ArcGIS reports query errors with HTTP 200 and an error body,
            # so this has to be checked explicitly.
            error = payload["error"]
            raise HttpError(
                f"arcgis error {error.get('code')}: {error.get('message')}",
                status=error.get("code") if isinstance(error.get("code"), int) else 400,
            )
        features = payload.get("features")
        if features is None:
            return None
        return [f.get("attributes", {}) for f in features if isinstance(f, dict)]


def _find_oid_field(fields: Sequence[Dict[str, Any]]) -> str:
    for field_def in fields:
        if field_def.get("type") == "esriFieldTypeOID":
            return str(field_def.get("name"))
    return "OBJECTID"


def _row_identity(row: Dict[str, Any]) -> str:
    for key in ("OBJECTID", "objectid", "OBJECTID_1", "FID", "GlobalID"):
        if row.get(key) is not None:
            return f"{key}={row[key]}"
    return repr(sorted((k, str(v)) for k, v in row.items()))[:500]


def layer_urls(service_url: str, max_layers: int = 6) -> List[str]:
    """Expand a service URL into candidate layer URLs.

    Hub often reports the service root rather than a specific layer.
    """
    root = service_url.rstrip("/")
    if root.rsplit("/", 1)[-1].isdigit():
        return [root]
    return [f"{root}/{i}" for i in range(max_layers)]
