"""Nationwide source discovery.

There is no national building-permit database. Coverage has to be
assembled jurisdiction by jurisdiction, and a hand-maintained list of a few
hundred endpoints both misses most of the country and rots as agencies
republish their layers.

Instead this module treats discovery as part of the agent's job, using the
two catalogs that index public permit data at national scale:

* **Socrata Discovery API** (``api.us.socrata.com``) — every dataset on
  every public Socrata portal, searchable by keyword and domain.
* **ArcGIS Hub** (``hub.arcgis.com``) — roughly ten thousand permit-ish
  feature layers published by counties and cities.

Catalog hits are only *candidates*. Each one is probed: introspect the
schema, map fields, and require that it actually looks like a permit table
before it is admitted to the registry. That probe step is what keeps the
registry from filling up with inspection logs and zoning-case layers that
merely mention permits.

The resulting registry is cached on disk so routine harvest runs cost
nothing extra, and a curated seed file can pin sources verified by hand.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from .connectors import get_connector
from .connectors.arcgis import layer_urls
from .connectors.socrata import resource_url
from .httpclient import HttpError, PoliteClient
from .models import SourceRef

log = logging.getLogger("dcpermits.discovery")

SOCRATA_CATALOG = "https://api.us.socrata.com/api/catalog/v1"
ARCGIS_HUB = "https://hub.arcgis.com/api/v3/datasets"

DATA_DIR = Path(__file__).parent / "data"

# Catalog search terms. Broad on purpose — the probe stage does the
# filtering, and a term like "certificate of occupancy" surfaces layers
# that "building permit" misses.
CATALOG_QUERIES = (
    "building permits",
    "construction permits",
    "commercial permits",
    "permits issued",
    "building permit applications",
)

# A candidate must look like permits, not inspections or zoning cases.
_NAME_POSITIVE = re.compile(
    r"permit|construction|certificate\s+of\s+occupancy|building\s+application", re.I
)
_NAME_NEGATIVE = re.compile(
    r"\b(?:parking|pet|dog|animal|fishing|hunting|burn|special\s+event|street\s+closure"
    r"|block\s+party|film|solicitation|peddler|vendor|garage\s+sale|tree\s+removal"
    r"|right[\s\-]of[\s\-]way|encroachment|banner|liquor|alcohol|marriage|taxi"
    r"|short[\s\-]term\s+rental|sign"
    # Public-space and transportation permits: a city DOT publishes plenty
    # of "permit" layers that have nothing to do with construction.
    r"|public\s+space|sidewalk|valet|dumpster|excavation|utility\s+cut"
    r"|driveway|curb\s+cut|scaffold|crane|oversize|towing|newsrack"
    # Inspections and reviews reference permits without being permit records.
    # Trailing ``s?`` matters: the closing \b would otherwise let the
    # plural form ("Permit Inspections") slip through.
    r"|inspections?|violations?|complaints?|code\s+enforcement|plan\s+reviews?)\b",
    re.I,
)

# US state names/abbreviations, used to attribute a source to a state.
STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC",
}


class Registry:
    """On-disk collection of known sources."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.sources: Dict[str, SourceRef] = {}

    # ------------------------------------------------------------------ io

    def load(self) -> "Registry":
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    payload = json.load(fh)
            except (OSError, json.JSONDecodeError) as exc:
                log.warning("cannot read registry %s: %s", self.path, exc)
                return self
            for entry in payload.get("sources", []):
                try:
                    source = SourceRef.from_dict(entry)
                except TypeError:
                    continue
                self.sources[source.source_id] = source
        return self

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "sources": [s.to_dict() for s in sorted(
                self.sources.values(), key=lambda s: s.source_id
            )],
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=False)
        tmp.replace(self.path)

    def load_seed(self, seed_path: Optional[Path] = None) -> "Registry":
        """Merge hand-curated sources, which win over discovered ones."""
        seed_path = seed_path or (DATA_DIR / "seed_sources.json")
        if not Path(seed_path).exists():
            return self
        try:
            with open(seed_path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("cannot read seed %s: %s", seed_path, exc)
            return self
        for entry in payload.get("sources", []):
            try:
                source = SourceRef.from_dict(entry)
            except TypeError:
                continue
            self.sources[source.source_id] = source
        return self

    # --------------------------------------------------------------- access

    def add(self, source: SourceRef) -> bool:
        """Add a source. Returns True if it was new."""
        is_new = source.source_id not in self.sources
        self.sources[source.source_id] = source
        return is_new

    def verified(self) -> List[SourceRef]:
        return [s for s in self.sources.values() if s.verified]

    def select(self, states: Optional[Sequence[str]] = None,
               connectors: Optional[Sequence[str]] = None,
               only_verified: bool = True) -> List[SourceRef]:
        chosen = self.verified() if only_verified else list(self.sources.values())
        if states:
            wanted = {s.upper() for s in states}
            chosen = [s for s in chosen if (s.state or "").upper() in wanted]
        if connectors:
            wanted_connectors = set(connectors)
            chosen = [s for s in chosen if s.connector in wanted_connectors]
        return sorted(chosen, key=lambda s: (s.state or "ZZ", s.jurisdiction))

    def __len__(self) -> int:
        return len(self.sources)


class Discovery:
    """Finds and validates candidate permit sources."""

    def __init__(self, client: PoliteClient, registry: Registry):
        self.client = client
        self.registry = registry

    # ------------------------------------------------------------- catalogs

    def search_socrata(self, query: str, limit: int = 100) -> List[SourceRef]:
        """Search the Socrata Discovery API for permit datasets."""
        candidates: List[SourceRef] = []
        offset = 0
        page = min(100, limit)
        while len(candidates) < limit:
            try:
                payload = self.client.get_json(SOCRATA_CATALOG, params={
                    "q": query,
                    "only": "dataset",
                    "limit": page,
                    "offset": offset,
                })
            except HttpError as exc:
                log.warning("socrata catalog query failed (%s): %s", query, exc)
                break
            results = payload.get("results") or []
            if not results:
                break
            for item in results:
                candidate = self._socrata_candidate(item)
                if candidate:
                    candidates.append(candidate)
            if len(results) < page:
                break
            offset += len(results)
        return candidates[:limit]

    def _socrata_candidate(self, item: Dict) -> Optional[SourceRef]:
        resource = item.get("resource") or {}
        metadata = item.get("metadata") or {}
        name = resource.get("name") or ""
        dataset_id = resource.get("id")
        domain = metadata.get("domain")
        if not dataset_id or not domain:
            return None
        if not _looks_like_permits(name):
            return None
        # Non-US portals show up in the catalog (Calgary, Edmonton). The
        # task is nationwide US coverage, so drop them.
        if _is_non_us(f"{domain} {name}"):
            return None

        jurisdiction = _jurisdiction_from_domain(domain)
        return SourceRef(
            source_id=f"socrata:{domain}:{dataset_id}",
            connector="socrata",
            endpoint=resource_url(domain, dataset_id),
            jurisdiction=jurisdiction,
            state=_state_from_text(f"{domain} {name}"),
            notes=name,
        )

    def search_arcgis_hub(self, query: str, limit: int = 100) -> List[SourceRef]:
        """Search ArcGIS Hub for permit feature layers."""
        candidates: List[SourceRef] = []
        page_size = min(100, limit)
        page_number = 1
        while len(candidates) < limit:
            params = {
                "q": query,
                "page[size]": page_size,
                "page[number]": page_number,
            }
            try:
                payload = self.client.get_json(ARCGIS_HUB, params=params)
            except HttpError as exc:
                log.warning("arcgis hub query failed (%s): %s", query, exc)
                break
            data = payload.get("data") or []
            if not data:
                break
            for item in data:
                candidate = self._hub_candidate(item)
                if candidate:
                    candidates.append(candidate)
            if len(data) < page_size:
                break
            page_number += 1
            # Hub paginates deeply; cap it so one query cannot run forever.
            if page_number > 20:
                break
        return candidates[:limit]

    def _hub_candidate(self, item: Dict) -> Optional[SourceRef]:
        attributes = item.get("attributes") or {}
        name = attributes.get("name") or ""
        url = attributes.get("url") or ""
        if not url or not _looks_like_permits(name):
            return None
        # Only feature/map services are queryable; Hub also indexes web
        # apps and PDFs.
        if not re.search(r"/(?:Feature|Map)Server", url, re.I):
            return None

        owner = attributes.get("owner") or ""
        region = attributes.get("region") or ""
        # The endpoint host carries the strongest jurisdiction signal, and
        # is also what reveals a non-US publisher.
        if _is_non_us(f"{url} {name} {owner}"):
            return None

        jurisdiction = _clean_jurisdiction(name, owner, url)
        return SourceRef(
            source_id=f"arcgis:{_slug(url)}",
            connector="arcgis",
            endpoint=url,
            jurisdiction=jurisdiction,
            state=_state_from_text(" ".join([url, name, owner, str(region)])),
            notes=name,
            record_count=attributes.get("recordCount"),
        )

    # ----------------------------------------------------------------- probe

    def probe(self, candidate: SourceRef) -> List[SourceRef]:
        """Validate a candidate by introspecting it.

        Returns the validated source(s) — plural because an ArcGIS service
        root can contain several layers, and we do not know in advance
        which one holds the permits.
        """
        if candidate.connector == "arcgis":
            return self._probe_arcgis(candidate)
        return self._probe_simple(candidate)

    def _probe_simple(self, candidate: SourceRef) -> List[SourceRef]:
        connector = get_connector(candidate.connector, self.client)
        try:
            described = connector.describe(candidate)
        except HttpError as exc:
            log.debug("probe failed for %s: %s", candidate.source_id, exc)
            return []
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("probe error for %s: %s", candidate.source_id, exc)
            return []
        return [described] if self._is_usable(described) else []

    def _probe_arcgis(self, candidate: SourceRef) -> List[SourceRef]:
        connector = get_connector("arcgis", self.client)
        found: List[SourceRef] = []
        for layer_url in layer_urls(candidate.endpoint):
            probe_source = SourceRef(
                source_id=f"arcgis:{_slug(layer_url)}",
                connector="arcgis",
                endpoint=layer_url,
                jurisdiction=candidate.jurisdiction,
                state=candidate.state,
                notes=candidate.notes,
            )
            try:
                described = connector.describe(probe_source)
            except HttpError:
                # A missing layer index is the normal way to learn a
                # service has fewer layers than we guessed — stop walking.
                break
            except Exception as exc:  # pragma: no cover - defensive
                log.debug("probe error for %s: %s", layer_url, exc)
                break
            if self._is_usable(described):
                found.append(described)
                # One permit layer per service is the overwhelming norm.
                break
        return found

    @staticmethod
    def _is_usable(source: SourceRef) -> bool:
        """A source is usable only if it yields analysable permit records."""
        field_map = source.field_map or {}
        if not source.verified:
            return False
        # Need identity and at least one date, or there is nothing to trend.
        has_identity = bool(field_map.get("permit_number") or field_map.get("description"))
        has_date = bool(field_map.get("issued_date") or field_map.get("applied_date")
                        or field_map.get("final_date"))
        # Need somewhere for a keyword to live.
        has_text = bool(source.text_fields)
        return has_identity and has_date and has_text

    # ------------------------------------------------------------------ run

    def run(self, queries: Iterable[str] = CATALOG_QUERIES,
            per_query: int = 60, max_probes: int = 200,
            include_socrata: bool = True, include_arcgis: bool = True
            ) -> Dict[str, int]:
        """Discover, probe and register sources. Returns a summary."""
        candidates: Dict[str, SourceRef] = {}
        for query in queries:
            if include_socrata:
                for candidate in self.search_socrata(query, limit=per_query):
                    candidates.setdefault(candidate.source_id, candidate)
            if include_arcgis:
                for candidate in self.search_arcgis_hub(query, limit=per_query):
                    candidates.setdefault(candidate.source_id, candidate)

        stats = {"candidates": len(candidates), "probed": 0, "verified": 0, "new": 0}

        for candidate in list(candidates.values())[:max_probes]:
            # Already-verified sources need no re-probe.
            existing = self.registry.sources.get(candidate.source_id)
            if existing and existing.verified:
                continue
            stats["probed"] += 1
            for source in self.probe(candidate):
                stats["verified"] += 1
                if self.registry.add(source):
                    stats["new"] += 1

        return stats


# ------------------------------------------------------------------ helpers


def _looks_like_permits(name: str) -> bool:
    if not name:
        return False
    if _NAME_NEGATIVE.search(name):
        return False
    return bool(_NAME_POSITIVE.search(name))


def _is_non_us(text: str) -> bool:
    """Reject non-US sources.

    Both catalogs index Canadian, UK and Australian publishers. Note that
    a ``.ca`` country TLD is Canada, *not* California — ``maps1.brampton.ca``
    is an Ontario city, and naive state matching happily labels it "CA".
    """
    lowered = text.lower()
    if re.search(r"\.(?:ca|uk|au|nz|ie|fr|de|es|it|nl|se|no|dk|mx|br|jp|sg|za|in)(?:/|$)",
                 lowered):
        return True
    return bool(re.search(
        r"\b(?:calgary|edmonton|toronto|ottawa|vancouver|montreal|winnipeg|brampton|"
        r"mississauga|hamilton|kitchener|surrey|kelowna|guelph|barrie|burlington|"
        r"oshawa|windsor|saskatoon|regina|halifax|ontario\.ca|quebec)\b", lowered))


# Domain/owner keywords that pin a source to a state. Worth maintaining by
# hand for the largest markets, since catalog metadata rarely states the
# state and guessing from two-letter tokens is unreliable.
_DOMAIN_STATE_HINTS = {
    "chicago": "IL", "cityofchicago": "IL",
    "lacity": "CA", "sfgov": "CA", "sandiego": "CA", "sanjose": "CA",
    "longbeach": "CA", "sacramento": "CA", "oakland": "CA", "fresno": "CA",
    "marincounty": "CA", "sonomacounty": "CA", "coronaca": "CA",
    "cityofnewyork": "NY", "nyc": "NY", "buffalo": "NY", "rochester": "NY",
    "dallasopendata": "TX", "austintexas": "TX", "houston": "TX",
    "sanantonio": "TX", "fortworth": "TX", "elpaso": "TX", "wcad": "TX",
    "mesaaz": "AZ", "phoenix": "AZ", "tempe": "AZ", "tucson": "AZ",
    "maricopa": "AZ", "chandleraz": "AZ", "scottsdale": "AZ",
    "seattle": "WA", "tacoma": "WA", "spokane": "WA", "bellevue": "WA",
    "montgomerycountymd": "MD", "baltimore": "MD", "howardcounty": "MD",
    "cincinnati": "OH", "columbus": "OH", "cleveland": "OH", "dayton": "OH",
    "loudoun": "VA", "fairfax": "VA", "richmond": "VA", "virginiabeach": "VA",
    "princewilliam": "VA", "henrico": "VA", "chesterfield": "VA",
    "atlanta": "GA", "savannah": "GA", "gwinnett": "GA", "douglas": "GA",
    "denver": "CO", "coloradosprings": "CO", "aurora": "CO", "boulder": "CO",
    "cambridgema": "MA", "boston": "MA", "somervillema": "MA",
    "nashville": "TN", "memphis": "TN", "knoxville": "TN", "chattanooga": "TN",
    "charlotte": "NC", "raleigh": "NC", "durham": "NC", "greensboro": "NC",
    "portland": "OR", "eugene": "OR", "hillsboro": "OR", "gresham": "OR",
    "saltlake": "UT", "slc": "UT", "utah": "UT", "provo": "UT",
    "lasvegas": "NV", "reno": "NV", "clarkcountynv": "NV", "henderson": "NV",
    "desmoines": "IA", "cedarrapids": "IA", "davenport": "IA",
    "omaha": "NE", "lincoln": "NE",
    "kansascity": "MO", "stlouis": "MO", "springfieldmo": "MO",
    "indianapolis": "IN", "fortwayne": "IN",
    "milwaukee": "WI", "madison": "WI",
    "minneapolis": "MN", "stpaul": "MN",
    "detroit": "MI", "grandrapids": "MI",
    "philadelphia": "PA", "pittsburgh": "PA", "phila": "PA",
    "jacksonville": "FL", "miami": "FL", "tampa": "FL", "orlando": "FL",
    "gainesville": "FL", "tallahassee": "FL", "oxnard": "CA",
    "brla": "LA", "neworleans": "LA", "batonrouge": "LA",
    "oklahomacity": "OK", "tulsa": "OK",
    "littlerock": "AR", "boisecity": "ID", "anchorage": "AK",
    "albuquerque": "NM", "santafe": "NM", "cheyenne": "WY",
    "louisville": "KY", "lexington": "KY", "birmingham": "AL",
    "jacksonms": "MS", "charleston": "SC", "columbiasc": "SC",
    "providence": "RI", "hartford": "CT", "newhaven": "CT",
    "wilmingtonde": "DE", "newark": "NJ", "jerseycity": "NJ",
    "honolulu": "HI", "roseville": "CA", "dcgis": "DC", "washingtondc": "DC",
}

# Case-sensitive so a bare "in", "or", "me" or "ok" in ordinary prose does
# not get read as Indiana, Oregon, Maine or Oklahoma.
_STATE_CODE_RE = re.compile(
    r"(?:^|[\s,\-_.\(/])(" + "|".join(sorted(set(STATES.values()))) + r")(?:[\s,\-_.\)/]|$)"
)


def _state_from_text(text: str) -> Optional[str]:
    """Infer a US state, conservatively.

    Attribution feeds the ``--state`` filter and every per-state rollup, so
    a wrong answer is materially worse than no answer: it silently moves a
    market's permits into the wrong row. Each strategy below is applied
    only where it is reliable, and ``None`` is a perfectly good result.
    """
    if not text:
        return None
    lowered = text.lower()

    # 1. A ``.xx.us`` domain states it outright.
    match = re.search(r"\.([a-z]{2})\.us\b", lowered)
    if match and match.group(1).upper() in set(STATES.values()):
        return match.group(1).upper()

    # 2. A full state name is unambiguous.
    spaced = f" {lowered} "
    squashed = re.sub(r"[^a-z]", "", lowered)
    for name, code in STATES.items():
        if f" {name} " in spaced or name.replace(" ", "") in squashed:
            return code

    # 3. A known city/county portal keyword.
    for keyword, code in _DOMAIN_STATE_HINTS.items():
        if keyword in squashed:
            return code

    # 4. A bare two-letter code, but only when capitalised as a state code
    #    is ("City of Jackson TN"), and not inside shouting text where
    #    every token is uppercase and the signal is meaningless.
    letters = [c for c in text if c.isalpha()]
    if letters and sum(1 for c in letters if c.isupper()) / len(letters) < 0.6:
        code_match = _STATE_CODE_RE.search(text)
        if code_match:
            return code_match.group(1)
    return None


# Portal hostnames whose derived name would otherwise be unreadable.
# "citydata.mesaaz.gov" mechanically becomes "Citydata Mesaaz"; these are
# the places where a human-readable label is worth hardcoding, since
# jurisdiction is the primary grouping key in every report.
_PORTAL_DISPLAY_NAMES = {
    "mesaaz": "Mesa",
    "austintexas": "Austin",
    "lacity": "Los Angeles",
    "dallasopendata": "Dallas",
    "cityofchicago": "Chicago",
    "cityofnewyork": "New York City",
    "montgomerycountymd": "Montgomery County",
    "wcad": "Williamson County",
    "dcgis": "Washington, DC",
    "brla": "Baton Rouge",
    "cambridgema": "Cambridge",
    "coronaca": "Corona",
    "sandiegocounty": "San Diego County",
    "sonomacounty": "Sonoma County",
    "marincounty": "Marin County",
    "cincinnatioh": "Cincinnati",
    "cityofgainesville": "Gainesville",
    "sfgov": "San Francisco",
    "clarkcountynv": "Clark County",
    "tempegov": "Tempe",
    "seattlegov": "Seattle",
}

# Subdomains that name the portal product rather than the jurisdiction.
_PORTAL_PREFIXES = (
    "data", "opendata", "open", "performance", "gis", "maps", "services",
    "cos-data", "internal", "explore", "hub", "citydata", "datahub",
    "corstat", "www", "results", "insights", "analytics", "stat",
)


def _jurisdiction_from_domain(domain: str) -> str:
    host = domain.lower()
    # Strip the portal-product subdomain, possibly more than one deep
    # ("internal-sandiegocounty.data.socrata.com").
    host = re.sub(r"\.(?:data\.socrata\.com|demo\.socrata\.com|socrata\.com)$", "", host)
    for _ in range(3):
        # The trailing \d* catches numbered hosts like "maps2.".
        stripped = re.sub(rf"^(?:{'|'.join(_PORTAL_PREFIXES)})\d*[.\-]", "", host)
        if stripped == host:
            break
        host = stripped
    host = re.sub(r"\.(?:gov|org|com|net|us)$", "", host)
    host = re.sub(r"\.[a-z]{2}$", "", host)

    squashed = re.sub(r"[^a-z0-9]", "", host)
    if squashed in _PORTAL_DISPLAY_NAMES:
        return _PORTAL_DISPLAY_NAMES[squashed]

    host = re.sub(r"^(?:cityof|countyof|city_of|county_of)", "", host)
    squashed = re.sub(r"[^a-z0-9]", "", host)
    if squashed in _PORTAL_DISPLAY_NAMES:
        return _PORTAL_DISPLAY_NAMES[squashed]

    parts = [p for p in re.split(r"[.\-_]", host) if p]
    return " ".join(p.capitalize() for p in parts) or domain


# Leftovers that describe the dataset, not the place that published it.
# "Active Building Permits - PROD" reduces to "Active", which would show up
# in reports as if it were a market.
_GENERIC_LABELS = {
    "active", "approved", "issued", "current", "all", "new", "open", "closed",
    "prod", "test", "final", "public", "open data", "copy", "view", "layer",
    "master", "historical", "archive", "draft", "data", "gis", "gisdata",
    "gis data", "feature", "feature layer", "web", "map", "gdb", "gcs",
    "recent", "pending", "complete", "completed", "combined", "monthly",
    "annual", "yearly", "quarterly", "daily", "weekly", "total", "summary",
}


def _is_generic_label(text: str) -> bool:
    return text.strip().strip("-–—_ ").lower() in _GENERIC_LABELS


def _clean_jurisdiction(name: str, owner: str, url: str = "") -> str:
    """Derive a readable jurisdiction from Hub metadata.

    Tried in order of reliability: the layer name (often "City of Jackson
    TN Building Permits"), then the endpoint host, then the publisher
    handle — which is the least reliable, being things like
    "MaricopaCountyGIS" or "hwitwicki".
    """
    # Strip all leading review/status tags ("[REVIEW] [DAR] ...") and the
    # generic "... Permits" tail.
    cleaned = re.sub(r"^(?:\[[^\]]*\]\s*)+", "", name or "").strip()
    cleaned = re.sub(
        r"\b(?:building|construction|commercial|residential|active|approved)?"
        r"\s*permits?\b.*$", "", cleaned, flags=re.I,
    ).strip(" -–—_")
    # Drop a leading month/year prefix ("December 2025 Building Permits").
    cleaned = re.sub(
        r"^(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|"
        r"dec(?:ember)?)\s*\d{0,4}\s*", "", cleaned, flags=re.I).strip()
    if len(cleaned) >= 3 and not _is_generic_label(cleaned):
        return cleaned

    if url:
        host = urllib.parse.urlsplit(url).netloc
        if host:
            derived = _jurisdiction_from_domain(host)
            # Reject the shared ArcGIS Online hosts, which say nothing
            # about who published the layer.
            if derived and not re.search(
                r"arcgis|esri|amazonaws|cloudfront", derived, re.I
            ):
                return derived

    if owner:
        # Owner handles look like "MaricopaCountyGIS" or "city_of_tigard".
        spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", owner)
        spaced = re.sub(r"[_\-]+", " ", spaced)
        spaced = re.sub(r"\b(?:gis|opendata|open data|admin|prod)\b", "",
                        spaced, flags=re.I)
        return " ".join(w.capitalize() for w in spaced.split()) or owner
    return name or "unknown"


def _slug(url: str) -> str:
    slug = re.sub(r"^https?://", "", url.lower())
    slug = re.sub(r"/(?:arcgis|rest|services)/", "/", slug)
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
    return slug[:120]
