"""Discovery tests.

Cases here are drawn from what live catalog sweeps actually returned:
Canadian portals, city-DOT permit layers, and metadata that names no state
at all. Mis-attributing a source is worse than skipping it, because it
silently moves permits into the wrong market.
"""

from __future__ import annotations

import pytest

from dcpermits.discovery import (
    Discovery,
    Registry,
    _clean_jurisdiction,
    _is_non_us,
    _jurisdiction_from_domain,
    _looks_like_permits,
    _slug,
    _state_from_text,
)
from dcpermits.models import SourceRef

# --------------------------------------------------------------- name filter


@pytest.mark.parametrize("name", [
    "Building Permits",
    "Construction Permits Issued",
    "Commercial Permits",
    "Certificate of Occupancy",
    "Building Permit Applications",
])
def test_permit_datasets_are_accepted(name):
    assert _looks_like_permits(name)


@pytest.mark.parametrize("name", [
    "Public Space Permits",        # DC DOT
    "Sign Permits",
    "Parking Permits",
    "Special Event Permits",
    "Dog Licenses and Permits",
    "Permit Inspections",          # plural must not slip past
    "Building Permit Violations",
    "Tree Removal Permits",
    "Right-of-Way Permits",
    "Short-Term Rental Permits",
    "",
])
def test_non_construction_permits_are_rejected(name):
    assert not _looks_like_permits(name)


# ------------------------------------------------------------------- non-US


@pytest.mark.parametrize("text", [
    "https://maps1.brampton.ca/arcgis/rest/services/BuildingPermit",
    "data.calgary.ca Building Permits",
    "data.edmonton.ca General Building Permits",
    "Toronto Building Permits",
])
def test_non_us_sources_are_rejected(text):
    assert _is_non_us(text)


@pytest.mark.parametrize("text", [
    "https://data.cityofchicago.org/resource/ydr8-5enu.json",
    "citydata.mesaaz.gov",
    "data.roseville.ca.us",       # a .ca.us domain is California, not Canada
])
def test_us_sources_are_kept(text):
    assert not _is_non_us(text)


# ------------------------------------------------------------ state mapping


@pytest.mark.parametrize("text,expected", [
    ("data.cityofchicago.org Building Permits", "IL"),
    ("citydata.mesaaz.gov", "AZ"),
    ("data.montgomerycountymd.gov", "MD"),
    ("Loudoun County Building Permits", "VA"),
    ("City of Jackson TN Building Permits", "TN"),
    ("data.roseville.ca.us", "CA"),
    ("maps2.dcgis.dc.gov/dcgis/rest/services", "DC"),
    ("Permits for the State of Ohio", "OH"),
])
def test_state_is_inferred(text, expected):
    assert _state_from_text(text) == expected


@pytest.mark.parametrize("text", [
    "Building Permits in 2024",              # "in" is not Indiana
    "Permits or Applications",               # "or" is not Oregon
    "BUILDING PERMITS ME OK IN OR",          # shouting carries no signal
    "https://maps1.brampton.ca/arcgis",      # .ca is Canada, not California
    "",
    "Permits Premium",
])
def test_ambiguous_text_yields_no_state(text):
    """No state is a better answer than the wrong state."""
    assert _state_from_text(text) is None


# ---------------------------------------------------------- jurisdiction name


@pytest.mark.parametrize("domain,expected", [
    ("citydata.mesaaz.gov", "Mesa"),
    ("datahub.austintexas.gov", "Austin"),
    ("data.cityofchicago.org", "Chicago"),
    ("data.lacity.org", "Los Angeles"),
    ("www.dallasopendata.com", "Dallas"),
    ("data.montgomerycountymd.gov", "Montgomery County"),
    ("internal-sandiegocounty.data.socrata.com", "San Diego County"),
    ("cos-data.seattle.gov", "Seattle"),
    ("maps2.dcgis.dc.gov", "Washington, DC"),
])
def test_jurisdiction_from_domain(domain, expected):
    assert _jurisdiction_from_domain(domain) == expected


def test_jurisdiction_prefers_the_layer_name():
    assert _clean_jurisdiction(
        "Loudoun County Building Permits", "someowner", "https://x/y") == "Loudoun County"


def test_jurisdiction_falls_back_to_host_when_the_name_is_generic():
    """"Active Building Permits - PROD" reduces to a useless "Active"."""
    result = _clean_jurisdiction(
        "Building Permits", "dcgisopendata",
        "https://maps2.dcgis.dc.gov/dcgis/rest/services/FEEDS/DCRA/FeatureServer/10")
    assert result == "Washington, DC"


def test_jurisdiction_strips_review_tags_and_month_prefixes():
    result = _clean_jurisdiction(
        "[REVIEW] [DAR] December 2025 Building Permits", "owner", "")
    assert "REVIEW" not in result
    assert "December" not in result


def test_slug_is_stable_and_bounded():
    url = "https://services1.arcgis.com/ABC/arcgis/rest/services/Permits/FeatureServer/0"
    assert _slug(url) == _slug(url)
    assert len(_slug(url)) <= 120
    assert " " not in _slug(url)


# ---------------------------------------------------------------- candidates


def test_socrata_candidate_is_built_from_catalog_metadata(fake_client):
    discovery = Discovery(fake_client, Registry("unused.json"))
    candidate = discovery._socrata_candidate({
        "resource": {"id": "ydr8-5enu", "name": "Building Permits"},
        "metadata": {"domain": "data.cityofchicago.org"},
    })
    assert candidate is not None
    assert candidate.connector == "socrata"
    assert candidate.endpoint == "https://data.cityofchicago.org/resource/ydr8-5enu.json"
    assert candidate.state == "IL"
    assert candidate.jurisdiction == "Chicago"


def test_socrata_candidate_rejects_non_permit_datasets(fake_client):
    discovery = Discovery(fake_client, Registry("unused.json"))
    assert discovery._socrata_candidate({
        "resource": {"id": "aaaa-bbbb", "name": "Dog Licenses"},
        "metadata": {"domain": "data.example.gov"},
    }) is None


def test_hub_candidate_requires_a_queryable_service(fake_client):
    discovery = Discovery(fake_client, Registry("unused.json"))
    # A web-app URL is indexed by Hub but cannot be queried.
    assert discovery._hub_candidate({"attributes": {
        "name": "Building Permits Filter App",
        "url": "https://x.maps.arcgis.com/apps/webappviewer/index.html?id=651",
    }}) is None
    # A FeatureServer can.
    assert discovery._hub_candidate({"attributes": {
        "name": "Loudoun County Building Permits",
        "url": "https://services.arcgis.com/x/arcgis/rest/services/P/FeatureServer/0",
    }}) is not None


def test_usable_requires_identity_date_and_text():
    """A layer with no date column cannot support trend analysis."""
    no_date = SourceRef(
        source_id="s", connector="arcgis", endpoint="e", jurisdiction="j",
        field_map={"permit_number": "P", "description": "D"},
        text_fields=["D"], verified=True,
    )
    assert not Discovery._is_usable(no_date)

    complete = SourceRef(
        source_id="s", connector="arcgis", endpoint="e", jurisdiction="j",
        field_map={"permit_number": "P", "description": "D", "issued_date": "I"},
        text_fields=["D"], verified=True,
    )
    assert Discovery._is_usable(complete)


def test_usable_requires_verified_flag():
    source = SourceRef(
        source_id="s", connector="arcgis", endpoint="e", jurisdiction="j",
        field_map={"permit_number": "P", "issued_date": "I"},
        text_fields=["P"], verified=False,
    )
    assert not Discovery._is_usable(source)


# ------------------------------------------------------------------ registry


def test_registry_round_trips(tmp_registry):
    source = SourceRef(
        source_id="socrata:x", connector="socrata", endpoint="https://x",
        jurisdiction="X", state="VA", field_map={"description": "d"},
        text_fields=["d"], address_parts=["street_number", "street_name"],
        verified=True,
    )
    registry = Registry(tmp_registry)
    registry.add(source)
    registry.save()

    reloaded = Registry(tmp_registry).load()
    assert len(reloaded) == 1
    restored = reloaded.sources["socrata:x"]
    assert restored.field_map == {"description": "d"}
    assert restored.address_parts == ["street_number", "street_name"]
    assert restored.verified


def test_registry_add_reports_novelty(tmp_registry):
    registry = Registry(tmp_registry)
    source = SourceRef(source_id="a", connector="socrata", endpoint="e",
                       jurisdiction="j")
    assert registry.add(source) is True
    assert registry.add(source) is False


def test_registry_select_filters(tmp_registry):
    registry = Registry(tmp_registry)
    registry.add(SourceRef(source_id="a", connector="socrata", endpoint="e",
                           jurisdiction="A", state="VA", verified=True))
    registry.add(SourceRef(source_id="b", connector="arcgis", endpoint="e",
                           jurisdiction="B", state="AZ", verified=True))
    registry.add(SourceRef(source_id="c", connector="socrata", endpoint="e",
                           jurisdiction="C", state="VA", verified=False))

    assert len(registry.select()) == 2
    assert len(registry.select(only_verified=False)) == 3
    assert len(registry.select(states=["VA"])) == 1
    assert len(registry.select(connectors=["arcgis"])) == 1


def test_registry_tolerates_a_corrupt_file(tmp_registry):
    tmp_registry.write_text("{not json", encoding="utf-8")
    assert len(Registry(tmp_registry).load()) == 0


def test_registry_missing_file_is_empty(tmp_path):
    assert len(Registry(tmp_path / "nope.json").load()) == 0
