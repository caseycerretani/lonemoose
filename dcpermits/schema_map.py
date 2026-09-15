"""Heuristic field mapping.

The central problem in nationwide permit harvesting is that no two
jurisdictions name their columns the same way. A description lives under
``DESCRIPTION``, ``work_description``, ``PROJ_DESC``, ``scope_of_work``,
``submission_type_name`` or ``Field14`` depending on who built the layer.
Hardcoding a table of field names per jurisdiction does not scale to
thousands of sources and rots the moment a county re-publishes its layer.

So instead we score each provider field against a set of patterns per
canonical field and take the best match. Scoring rules:

* an exact match on a known alias wins outright
* otherwise regex patterns contribute weights
* fields whose provider type disagrees with the canonical field (a string
  column for ``valuation``) are penalised, not excluded, because plenty of
  feeds ship numbers as text
* negative patterns push away lookalikes — ``ISSUED_BY`` is not
  ``issued_date``, ``owner_phone`` is not ``owner``

Every mapping decision is explainable via :func:`explain_mapping`, which
matters when a harvest returns nonsense and you need to know why.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .models import CANONICAL_FIELDS

# Provider types that look numeric across ArcGIS/Socrata/CSV.
NUMERIC_TYPES = {
    "esriFieldTypeDouble", "esriFieldTypeSingle", "esriFieldTypeInteger",
    "esriFieldTypeSmallInteger", "esriFieldTypeBigInteger",
    "number", "money", "double", "integer", "float", "int", "numeric",
}
DATE_TYPES = {
    "esriFieldTypeDate", "esriFieldTypeTimestampOffset",
    "calendar_date", "date", "datetime", "timestamp", "floating_timestamp",
}
TEXT_TYPES = {
    "esriFieldTypeString", "text", "string", "url", "html",
}
# Geometry columns. Socrata's ``location``/``point`` columns hold GeoJSON
# dicts, and naming makes them look like an address field — mapping one to
# ``address`` yields a stringified dict for every record.
GEO_TYPES = {
    "point", "location", "multipoint", "polygon", "multipolygon", "line",
    "multiline", "geometry", "esriFieldTypeGeometry", "esriGeometryPoint",
}


class Rule:
    """Matching rules for one canonical field."""

    __slots__ = ("aliases", "patterns", "negative", "kind")

    def __init__(self, aliases: Sequence[str], patterns: Sequence[Tuple[str, float]],
                 negative: Sequence[str] = (), kind: str = "text"):
        self.aliases = {a.lower() for a in aliases}
        self.patterns = [(re.compile(p, re.I), w) for p, w in patterns]
        self.negative = [re.compile(p, re.I) for p in negative]
        self.kind = kind


# Ordered by how much downstream value the field carries, which also
# determines who gets first claim on a contested provider field.
RULES: Dict[str, Rule] = {
    "description": Rule(
        aliases=[
            "description", "work_description", "permit_description",
            "project_description", "proj_desc", "workdesc", "scope_of_work",
            "scope", "work_desc", "job_description", "descript", "desc",
            "permit_desc", "detailed_description", "work_performed",
            "project_name", "projectname", "work_type_description",
        ],
        patterns=[
            (r"(work|permit|project|job|proposed)[_\s-]*(desc|description|scope|narrative)", 10),
            (r"^desc", 7),
            (r"descript", 6),
            (r"scope", 5),
            (r"(project|proj)[_\s-]*(name|title)", 4),
            (r"comment|remark|narrative|summary", 3),
        ],
        negative=[r"code$", r"_id$", r"^id$", r"number$", r"type_?id"],
        kind="text",
    ),
    "permit_number": Rule(
        aliases=[
            "permit_number", "permitnumber", "permit_num", "permitnum",
            "permit_", "permit_id", "permitid", "permit", "record_id",
            "recordid", "case_number", "casenumber", "application_number",
            "app_number", "folio", "permit_no", "permitno", "number",
            "submission_number", "plan_number", "bldg_permit_no",
        ],
        patterns=[
            (r"permit[_\s-]*(num|number|no|id)", 10),
            (r"(case|application|app|record|submission)[_\s-]*(num|number|no|id)", 8),
            (r"^permit_?$", 7),
            (r"^(num|number|no)$", 4),
        ],
        negative=[r"type", r"status", r"count", r"objectid", r"^fid$"],
        kind="text",
    ),
    "issued_date": Rule(
        aliases=[
            "issued_date", "issue_date", "issuedate", "date_issued",
            "issued", "permit_issued_date", "issued_on", "dateissued",
            "issuedt", "permit_issue_date",
        ],
        patterns=[
            (r"issue[d]?[_\s-]*date", 10),
            (r"date[_\s-]*issue[d]?", 10),
            (r"^issued?$", 6),
        ],
        negative=[r"by$", r"staff", r"user", r"name", r"^issued_?to"],
        kind="date",
    ),
    "applied_date": Rule(
        aliases=[
            "applied_date", "application_date", "app_date", "date_applied",
            "applieddate", "submit_date", "submitted_date", "date_submitted",
            "filed_date", "date_filed", "created_at", "created_date",
            "application_start_date", "apply_date", "intake_date",
        ],
        patterns=[
            (r"(appl(y|ied|ication)|submit(ted)?|file[d]?|intake)[_\s-]*date", 10),
            (r"date[_\s-]*(appl(y|ied|ication)|submit(ted)?|file[d]?)", 10),
            (r"(created|entered|open(ed)?)[_\s-]*(date|at|on)", 6),
        ],
        negative=[r"by$", r"staff", r"user", r"name"],
        kind="date",
    ),
    "final_date": Rule(
        aliases=[
            "final_date", "finaled_date", "date_finaled", "completed_date",
            "completion_date", "date_completed", "co_date", "closed_date",
            "expiration_date", "date_closed",
        ],
        patterns=[
            (r"(final(ed)?|complet(e|ed|ion)|clos(e|ed)|expir\w*)[_\s-]*date", 10),
            (r"date[_\s-]*(final(ed)?|complet(e|ed)|clos(e|ed)|expir\w*)", 10),
            (r"c_?o[_\s-]*date", 5),
        ],
        negative=[r"by$", r"staff", r"user", r"inspector"],
        kind="date",
    ),
    "valuation": Rule(
        aliases=[
            "valuation", "job_value", "jobvalue", "declared_valuation",
            "estimated_cost", "est_cost", "construction_cost", "total_value",
            "permit_value", "reported_cost", "value", "total_valuation",
            "project_cost", "project_valuation", "cost", "job_cost",
            "estimated_value", "improvement_value", "construction_value",
            "total_job_valuation", "valuation_amount", "contract_value",
        ],
        patterns=[
            (r"valuation", 10),
            (r"(job|project|construction|permit|total|declared|reported|estimated|improvement)"
             r"[_\s-]*(value|cost|valuation|amount)", 9),
            (r"(value|cost)[_\s-]*(of[_\s-]*work|amount)?$", 5),
            (r"^(cost|value|amount)$", 4),
        ],
        # Fee is not valuation — conflating them understates projects by
        # three orders of magnitude.
        negative=[
            r"fee", r"tax", r"paid", r"balance", r"due", r"assess",
            r"land[_\s-]*value", r"date", r"per[_\s-]*(sq|unit)",
        ],
        kind="number",
    ),
    "square_feet": Rule(
        aliases=[
            "square_feet", "squarefeet", "sq_ft", "sqft", "total_sq_ft",
            "area", "floor_area", "building_area", "total_area",
            "gross_floor_area", "total_finished_area", "sq_feet",
            "building_sqft", "square_footage",
        ],
        patterns=[
            (r"(sq(uare)?)[_\s-]*(ft|feet|footage)", 10),
            (r"^sqft", 9),
            (r"(floor|building|gross|total|construction)[_\s-]*area", 8),
            (r"^area$", 4),
        ],
        negative=[r"lot", r"land", r"parcel", r"acre", r"shape", r"geometry"],
        kind="number",
    ),
    "address": Rule(
        aliases=[
            "address", "site_address", "siteaddress", "full_address",
            "location", "street_address", "project_address", "addr",
            "original_address1", "permit_address", "address1",
            "location_address", "job_address", "situs_address", "street",
        ],
        patterns=[
            (r"(site|project|job|full|street|permit|situs|primary)[_\s-]*address", 10),
            (r"^address", 9),
            (r"addr", 6),
            (r"^location$", 5),
            (r"^street", 4),
        ],
        negative=[
            r"mail", r"owner", r"contractor", r"applicant", r"contact",
            r"city", r"state", r"zip", r"_lat", r"_lng", r"_lon",
            # Address *components* are assembled by find_address_parts;
            # mapping one here would put a bare "N" in the address field.
            r"^street_?(?:number|no|num|name|type|suffix|direction|dir|prefix)$",
            r"^house_?(?:number|no|num)$", r"direction$", r"^pre_?dir",
        ],
        kind="text",
    ),
    "city": Rule(
        aliases=["city", "municipality", "town", "site_city", "original_city", "jurisdiction"],
        patterns=[(r"^city$", 10), (r"(site|project|permit)[_\s-]*city", 9),
                  (r"municipal|^town$", 5)],
        negative=[r"mail", r"owner", r"contractor", r"applicant", r"contact", r"code", r"limit"],
        kind="text",
    ),
    "zipcode": Rule(
        aliases=["zip", "zipcode", "zip_code", "postal_code", "original_zip", "site_zip"],
        patterns=[(r"zip", 10), (r"postal", 8)],
        # "contact" matters here: feeds like Chicago's carry a numbered
        # contact block (contact_10_zipcode) whose ZIP is the applicant's
        # mailing address, not the job site.
        negative=[r"mail", r"owner", r"contractor", r"applicant", r"contact", r"plus4"],
        kind="text",
    ),
    "latitude": Rule(
        aliases=["latitude", "lat", "y", "location_lat", "site_lat", "point_y"],
        patterns=[(r"^lat(itude)?$", 10), (r"lat(itude)?$", 8), (r"^(point_)?y$", 5)],
        negative=[r"long", r"lng"],
        kind="number",
    ),
    "longitude": Rule(
        aliases=["longitude", "long", "lon", "lng", "x", "location_lng", "point_x"],
        patterns=[(r"^lon(gitude)?$|^lng$|^long$", 10), (r"(lon|lng)(gitude)?$", 8),
                  (r"^(point_)?x$", 5)],
        negative=[r"lat"],
        kind="number",
    ),
    "permit_type": Rule(
        aliases=[
            "permit_type", "permittype", "type", "permit_class",
            "permit_type_desc", "application_type", "record_type",
            "submission_type_name", "permit_category", "worktype",
            "permit_type_description", "casetype",
        ],
        patterns=[
            (r"permit[_\s-]*(type|class|category|kind)", 10),
            (r"(application|record|case|submission)[_\s-]*type", 8),
            (r"^type", 5),
            (r"type$", 4),
        ],
        negative=[r"_id$", r"code$", r"sub_?type", r"contact", r"fee", r"inspection"],
        kind="text",
    ),
    "work_class": Rule(
        aliases=[
            "work_class", "workclass", "work_type", "worktype",
            "permit_subtype", "sub_type", "subtype", "class",
            "construction_type", "use_type", "occupancy_type",
            "work_class_group", "permit_workclass",
        ],
        patterns=[
            (r"work[_\s-]*(class|type|group)", 10),
            (r"(sub[_\s-]*type|subtype)", 8),
            (r"(construction|occupancy|use)[_\s-]*(type|class)", 6),
            (r"^class$", 4),
        ],
        negative=[r"_id$", r"code$"],
        kind="text",
    ),
    "status": Rule(
        aliases=[
            "status", "permit_status", "current_status", "status_desc",
            "statuscurrent", "record_status", "app_status", "status_description",
        ],
        patterns=[(r"status", 10), (r"^state$", 3)],
        negative=[r"_id$", r"date", r"code$"],
        kind="text",
    ),
    "applicant": Rule(
        aliases=[
            "applicant", "applicant_name", "applicantname", "applicant_full_name",
            "apply_name", "submitter", "agent_name", "applicant_business_name",
        ],
        patterns=[
            (r"applicant[_\s-]*(name|business)?", 10),
            (r"(agent|submitter)[_\s-]*name", 6),
        ],
        negative=[r"phone", r"email", r"address", r"city", r"state", r"zip", r"_id$", r"type"],
        kind="text",
    ),
    "owner": Rule(
        aliases=[
            "owner", "owner_name", "ownername", "property_owner",
            "owner_full_name", "ownerlegalname", "owner_business_name",
            "tenant_name",
        ],
        patterns=[
            (r"owner[_\s-]*(name|legal|business|full)?", 10),
            (r"(property|building|tenant)[_\s-]*(owner|name)", 8),
        ],
        negative=[r"phone", r"email", r"address", r"city", r"state", r"zip", r"_id$",
                  r"occupied", r"type"],
        kind="text",
    ),
    "contractor": Rule(
        aliases=[
            "contractor", "contractor_name", "contractorname",
            "contractor_business_name", "gc_name", "general_contractor",
            "company_name", "firm_name", "builder",
        ],
        patterns=[
            (r"contractor[_\s-]*(name|business|company)?", 10),
            (r"(general[_\s-]*contractor|^gc_|builder)", 8),
            (r"(company|firm|business)[_\s-]*name", 5),
        ],
        negative=[r"phone", r"email", r"address", r"city", r"state", r"zip",
                  r"license", r"_id$", r"type", r"number"],
        kind="text",
    ),
}

# Minimum score to accept a heuristic (non-alias) match. Tuned so obvious
# lookalikes fall below the bar rather than producing confident nonsense.
MIN_SCORE = 4.0


def _type_affinity(field_kind: str, provider_type: Optional[str]) -> float:
    """Multiplier reflecting whether the provider type suits the field."""
    if not provider_type:
        return 1.0
    ptype = str(provider_type).strip()
    ptype_lower = ptype.lower()

    def in_set(group: Iterable[str]) -> bool:
        return ptype in group or ptype_lower in {g.lower() for g in group}

    # Geometry is never a usable value for any canonical field; lat/lon are
    # read from their own numeric columns.
    if in_set(GEO_TYPES):
        return 0.0

    if field_kind == "date":
        if in_set(DATE_TYPES):
            return 1.3
        # Dates-as-strings and epoch-as-number are both common; normalize.py
        # copes, so only mildly discourage.
        return 0.8
    if field_kind == "number":
        if in_set(NUMERIC_TYPES):
            return 1.3
        if in_set(DATE_TYPES):
            return 0.2
        return 0.75
    # text
    if in_set(TEXT_TYPES):
        return 1.15
    if in_set(DATE_TYPES):
        return 0.3
    return 0.9


def score_field(canonical: str, provider_name: str,
                provider_type: Optional[str] = None) -> float:
    """Score how well ``provider_name`` fits ``canonical``. 0 means no fit."""
    rule = RULES.get(canonical)
    if rule is None:
        return 0.0

    name = provider_name.strip()
    lowered = name.lower()
    # Normalize separators so "Permit Number", "permit-number" and
    # "PermitNumber" all compare alike.
    flattened = re.sub(r"[\s\-]+", "_", lowered)
    squashed = re.sub(r"[^a-z0-9]", "", lowered)

    for pattern in rule.negative:
        if pattern.search(flattened):
            return 0.0

    affinity = _type_affinity(rule.kind, provider_type)

    if flattened in rule.aliases or lowered in rule.aliases or squashed in {
        re.sub(r"[^a-z0-9]", "", a) for a in rule.aliases
    }:
        # Exact alias: high, fixed base so aliases always beat regex hits.
        return 100.0 * affinity

    best = 0.0
    for pattern, weight in rule.patterns:
        if pattern.search(flattened):
            best = max(best, float(weight))
    if best == 0.0:
        return 0.0
    return best * affinity


def map_fields(provider_fields: Sequence[Any]) -> Dict[str, str]:
    """Build a canonical -> provider field mapping.

    ``provider_fields`` may be a list of names, or of dicts carrying
    ``name``/``type`` (ArcGIS ``fields``, Socrata column metadata).

    Assignment is greedy over all (canonical, provider) pairs by descending
    score, so each provider field is claimed by at most one canonical field
    and vice versa. That prevents e.g. ``issue_date`` being mapped to both
    ``issued_date`` and ``applied_date``.
    """
    candidates = _normalize_provider_fields(provider_fields)

    scored: List[Tuple[float, str, str]] = []
    for canonical in CANONICAL_FIELDS:
        for name, ptype in candidates:
            score = score_field(canonical, name, ptype)
            if score >= MIN_SCORE:
                scored.append((score, canonical, name))

    # Descending score; ties broken deterministically so runs reproduce.
    scored.sort(key=lambda t: (-t[0], t[1], t[2]))

    mapping: Dict[str, str] = {}
    taken: set = set()
    for _score, canonical, name in scored:
        if canonical in mapping or name in taken:
            continue
        mapping[canonical] = name
        taken.add(name)
    return mapping


def explain_mapping(provider_fields: Sequence[Any], top_n: int = 3
                    ) -> Dict[str, List[Tuple[str, float]]]:
    """Return the ranked candidates per canonical field, for debugging."""
    candidates = _normalize_provider_fields(provider_fields)
    out: Dict[str, List[Tuple[str, float]]] = {}
    for canonical in CANONICAL_FIELDS:
        ranked = sorted(
            ((name, round(score_field(canonical, name, ptype), 2))
             for name, ptype in candidates),
            key=lambda t: -t[1],
        )
        out[canonical] = [r for r in ranked if r[1] > 0][:top_n]
    return out


def _normalize_provider_fields(provider_fields: Sequence[Any]
                               ) -> List[Tuple[str, Optional[str]]]:
    candidates: List[Tuple[str, Optional[str]]] = []
    for item in provider_fields:
        if isinstance(item, str):
            candidates.append((item, None))
        elif isinstance(item, dict):
            name = item.get("name") or item.get("fieldName") or item.get("id")
            if not name:
                continue
            ptype = item.get("type") or item.get("dataTypeName") or item.get("fieldType")
            candidates.append((str(name), str(ptype) if ptype else None))
    return candidates


# Address components, in the order they should be joined. Plenty of feeds
# (Chicago among them) have no single address column at all — only
# street_number / street_direction / street_name.
_ADDRESS_PART_PATTERNS: Sequence[Tuple[str, str]] = (
    ("number", r"^(?:street_?(?:number|no|num)|house_?(?:number|no|num)|addr_?number|stnum)$"),
    ("prefix", r"^(?:street_?(?:direction|dir|prefix)|pre_?dir(?:ection)?|addr_?prefix)$"),
    ("name", r"^(?:street_?name|st_?name|addr_?street|thoroughfare)$"),
    ("type", r"^(?:street_?(?:type|suffix)|st_?type|suf_?dir(?:ection)?|post_?dir)$"),
    ("unit", r"^(?:unit|unit_?(?:number|no)|suite|apt|apt_?(?:number|no))$"),
)


def find_address_parts(provider_fields: Sequence[Any]) -> List[str]:
    """Provider fields that together compose a street address.

    Returns them in join order, or an empty list if there is no usable
    combination. A lone street name without a number is not worth
    assembling, so require at least a number and a name.
    """
    candidates = _normalize_provider_fields(provider_fields)
    found: Dict[str, str] = {}
    for role, pattern in _ADDRESS_PART_PATTERNS:
        compiled = re.compile(pattern, re.I)
        for name, _ptype in candidates:
            flattened = re.sub(r"[\s\-]+", "_", name.strip().lower())
            if compiled.match(flattened) and role not in found:
                found[role] = name
    if "number" not in found or "name" not in found:
        return []
    return [found[role] for role, _p in _ADDRESS_PART_PATTERNS if role in found]


def pick_text_fields(provider_fields: Sequence[Any], limit: int = 4) -> List[str]:
    """Choose provider text fields worth pushing a keyword filter into.

    Server-side filtering is what makes a nationwide sweep affordable, so we
    want every plausibly descriptive text column, not just the single best
    ``description`` match.
    """
    candidates = _normalize_provider_fields(provider_fields)
    interesting = ("description", "permit_type", "work_class", "owner",
                   "applicant", "contractor")
    scored: List[Tuple[float, str]] = []
    for name, ptype in candidates:
        if ptype and not (
            str(ptype) in TEXT_TYPES
            or str(ptype).lower() in {t.lower() for t in TEXT_TYPES}
        ):
            continue
        best = max((score_field(c, name, ptype) for c in interesting), default=0.0)
        if best >= MIN_SCORE:
            scored.append((best, name))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [name for _s, name in scored[:limit]]
