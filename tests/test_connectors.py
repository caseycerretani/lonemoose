"""Connector tests, all offline.

The behaviours worth protecting here are the resilience ones: staged
query degradation, pagination, and the ArcGIS habit of reporting errors
with an HTTP 200 status.
"""

from __future__ import annotations

import pytest

from dcpermits.connectors import HarvestQuery, get_connector
from dcpermits.connectors.arcgis import ArcGISConnector, layer_urls
from dcpermits.connectors.base import build_text_predicate
from dcpermits.connectors.csvfeed import CsvConnector
from dcpermits.connectors.socrata import SocrataConnector, _split_endpoint
from dcpermits.httpclient import HttpError
from dcpermits.models import SourceRef

SOCRATA_ENDPOINT = "https://data.example.gov/resource/abcd-1234.json"

SOCRATA_VIEW = {
    "columns": [
        {"fieldName": "permit_", "dataTypeName": "text"},
        {"fieldName": "work_description", "dataTypeName": "text"},
        {"fieldName": "issue_date", "dataTypeName": "calendar_date"},
        {"fieldName": "reported_cost", "dataTypeName": "number"},
        {"fieldName": "street_number", "dataTypeName": "text"},
        {"fieldName": "street_name", "dataTypeName": "text"},
        {"fieldName": ":internal", "dataTypeName": "text"},
    ]
}

SOCRATA_ROWS = [
    {
        "permit_": "P-1",
        "work_description": "NEW HYPERSCALE DATA CENTER SHELL",
        "issue_date": "2026-02-10T00:00:00.000",
        "reported_cost": "250000000",
        "street_number": "100",
        "street_name": "SERVER RD",
    },
    {
        "permit_": "P-2",
        "work_description": "VOICE AND DATA CABLING FOR OFFICE",
        "issue_date": "2026-02-11T00:00:00.000",
        "reported_cost": "40000",
        "street_number": "200",
        "street_name": "MAIN ST",
    },
]


def socrata_source():
    return SourceRef(
        source_id="socrata:test", connector="socrata",
        endpoint=SOCRATA_ENDPOINT, jurisdiction="Example", state="VA",
    )


# ------------------------------------------------------------------ socrata


def test_socrata_describe_maps_fields(fake_client):
    fake_client.add("/api/views/abcd-1234.json", SOCRATA_VIEW)
    described = SocrataConnector(fake_client).describe(socrata_source())

    assert described.verified
    assert described.field_map["description"] == "work_description"
    assert described.field_map["valuation"] == "reported_cost"
    assert described.date_field == "issue_date"
    assert described.address_parts == ["street_number", "street_name"]
    assert ":internal" not in described.field_map.values()


def test_socrata_describe_falls_back_to_row_sampling(fake_client):
    # A restricted metadata view must not stop the source from working.
    fake_client.add("/api/views/", HttpError("forbidden", status=403))
    fake_client.add("/resource/abcd-1234.json", [SOCRATA_ROWS[0]])
    described = SocrataConnector(fake_client).describe(socrata_source())
    assert described.field_map["description"] == "work_description"


def test_socrata_pushes_keyword_and_date_filters_server_side(fake_client):
    fake_client.add("/api/views/abcd-1234.json", SOCRATA_VIEW)
    connector = SocrataConnector(fake_client)
    source = connector.describe(socrata_source())

    fake_client.add("/resource/abcd-1234.json", SOCRATA_ROWS)
    list(connector.fetch_rows(source, HarvestQuery(
        keywords=["data center"], since="2026-01-01", limit=10)))

    sent = fake_client.queries_containing("$where")
    assert sent, "expected a server-side where clause"
    assert "DATA CENTER" in sent[0].upper()
    assert "2026-01-01" in sent[0]


def test_socrata_relaxes_query_when_server_rejects_it(fake_client):
    """A 400 on the keyword predicate must degrade, not fail the source."""
    fake_client.add("/api/views/abcd-1234.json", SOCRATA_VIEW)
    connector = SocrataConnector(fake_client)
    source = connector.describe(socrata_source())

    calls = {"n": 0}
    original = fake_client.get_json

    def flaky(url, params=None, headers=None):
        if params and "$where" in params:
            calls["n"] += 1
            raise HttpError("bad request", status=400)
        return original(url, params, headers)

    fake_client.get_json = flaky
    fake_client.add("/resource/abcd-1234.json", SOCRATA_ROWS)

    rows = list(connector.fetch_rows(source, HarvestQuery(
        keywords=["data center"], since="2026-01-01", limit=10)))

    assert calls["n"] > 0, "should have attempted a server-side filter first"
    # Fell back to a full scan, then filtered locally: only the real
    # data-center row survives.
    assert len(rows) == 1
    assert rows[0]["permit_"] == "P-1"


def test_socrata_to_permit_assembles_split_address(fake_client):
    fake_client.add("/api/views/abcd-1234.json", SOCRATA_VIEW)
    connector = SocrataConnector(fake_client)
    source = connector.describe(socrata_source())

    permit = connector.to_permit(source, SOCRATA_ROWS[0])
    assert permit.address == "100 SERVER RD"
    assert permit.valuation == 250_000_000.0
    assert permit.issued_date == "2026-02-10"
    assert permit.permit_number == "P-1"
    assert permit.state == "VA"


def test_socrata_paginates_until_short_page(fake_client):
    fake_client.add("/api/views/abcd-1234.json", SOCRATA_VIEW)
    connector = SocrataConnector(fake_client)
    source = connector.describe(socrata_source())

    pages = [[dict(SOCRATA_ROWS[0], permit_=f"P-{i}") for i in range(1000)], []]

    def paged(url, params=None, headers=None):
        if params and "$offset" in params:
            return pages.pop(0) if pages else []
        return SOCRATA_VIEW

    fake_client.get_json = paged
    rows = list(connector.fetch_rows(source, HarvestQuery(limit=2500)))
    assert len(rows) == 1000


@pytest.mark.parametrize("endpoint,expected", [
    ("https://data.example.gov/resource/abcd-1234.json", ("data.example.gov", "abcd-1234")),
    ("https://data.example.gov/resource/abcd-1234.csv", ("data.example.gov", "abcd-1234")),
    ("https://data.example.gov/resource/notanid.json", ("data.example.gov", None)),
])
def test_split_endpoint(endpoint, expected):
    assert _split_endpoint(endpoint) == expected


# ------------------------------------------------------------------- arcgis

ARCGIS_ENDPOINT = "https://services1.arcgis.com/x/arcgis/rest/services/Permits/FeatureServer/0"

ARCGIS_META = {
    "name": "Building Permits",
    "maxRecordCount": 2000,
    "objectIdField": "OBJECTID",
    "advancedQueryCapabilities": {"supportsPagination": True},
    "fields": [
        {"name": "OBJECTID", "type": "esriFieldTypeOID"},
        {"name": "PERMIT_NO", "type": "esriFieldTypeString"},
        {"name": "PROJ_DESC", "type": "esriFieldTypeString"},
        {"name": "ISSUE_DATE", "type": "esriFieldTypeDate"},
        {"name": "JOB_VALUE", "type": "esriFieldTypeDouble"},
    ],
}


def arcgis_source():
    return SourceRef(
        source_id="arcgis:test", connector="arcgis",
        endpoint=ARCGIS_ENDPOINT, jurisdiction="Example County", state="VA",
    )


def test_arcgis_describe_maps_nonobvious_field_names(fake_client):
    fake_client.add("/FeatureServer/0", ARCGIS_META)
    fake_client.add("/FeatureServer/0/query", {"count": 1234})
    described = ArcGISConnector(fake_client).describe(arcgis_source())

    assert described.verified
    assert described.field_map["description"] == "PROJ_DESC"
    assert described.field_map["permit_number"] == "PERMIT_NO"
    assert described.field_map["valuation"] == "JOB_VALUE"
    assert described.record_count == 1234


def test_arcgis_error_body_with_http_200_is_detected(fake_client):
    """ArcGIS returns query errors with a 200 status and an error body."""
    fake_client.add("/FeatureServer/0", ARCGIS_META)
    fake_client.add("/FeatureServer/0/query", {"count": 5})
    connector = ArcGISConnector(fake_client)
    source = connector.describe(arcgis_source())

    fake_client.add("/FeatureServer/0/query", {
        "error": {"code": 400, "message": "'Invalid field: DESCRIPTION'"}
    })
    # All variants fail, so no rows — but no crash either.
    assert list(connector.fetch_rows(source, HarvestQuery(limit=5))) == []


def test_arcgis_returns_attributes_and_classifies(fake_client):
    fake_client.add("/FeatureServer/0", ARCGIS_META)
    fake_client.add("/FeatureServer/0/query", {"count": 2})
    connector = ArcGISConnector(fake_client)
    source = connector.describe(arcgis_source())

    fake_client.add("/FeatureServer/0/query", {"features": [
        {"attributes": {
            "OBJECTID": 1,
            "PERMIT_NO": "B-99",
            "PROJ_DESC": "NEW DATA CENTER CAMPUS BUILDING 2",
            "ISSUE_DATE": 1771718400000,
            "JOB_VALUE": 310_000_000,
        }},
    ]})
    permits = list(connector.harvest(source, HarvestQuery(limit=5)))
    assert len(permits) == 1
    assert permits[0].permit_number == "B-99"
    assert permits[0].valuation == 310_000_000
    # Epoch milliseconds, correctly converted.
    assert permits[0].issued_date.startswith("2026-")


def test_arcgis_falls_back_to_oid_paging_without_pagination_support(fake_client):
    meta = dict(ARCGIS_META, advancedQueryCapabilities={"supportsPagination": False})
    fake_client.add("/FeatureServer/0", meta)
    fake_client.add("/FeatureServer/0/query", {"count": 1})
    connector = ArcGISConnector(fake_client)
    source = connector.describe(arcgis_source())

    batches = [
        {"features": [{"attributes": {"OBJECTID": 1, "PERMIT_NO": "A",
                                      "PROJ_DESC": "DATA CENTER"}}]},
        {"features": []},
    ]

    seen_params = []

    def paged(url, params=None, headers=None):
        seen_params.append(params or {})
        if params and "orderByFields" in params:
            return batches.pop(0) if batches else {"features": []}
        if params and "returnCountOnly" in params:
            return {"count": 1}
        return meta

    fake_client.get_json = paged
    rows = list(connector.fetch_rows(source, HarvestQuery(limit=50)))
    assert len(rows) == 1
    # OID paging is the fallback, and it must order by the OID field.
    oid_queries = [p for p in seen_params if "orderByFields" in p]
    assert oid_queries
    assert oid_queries[0]["orderByFields"] == "OBJECTID ASC"
    assert "OBJECTID > -1" in oid_queries[0]["where"]


def test_layer_urls_expands_service_root_but_not_a_layer():
    assert layer_urls("https://x/FeatureServer/3") == ["https://x/FeatureServer/3"]
    expanded = layer_urls("https://x/FeatureServer", max_layers=3)
    assert expanded == ["https://x/FeatureServer/0", "https://x/FeatureServer/1",
                        "https://x/FeatureServer/2"]


# ---------------------------------------------------------------------- csv

CSV_BODY = (
    "permit_number,work_description,issue_date,job_value\n"
    "C-1,NEW DATA CENTER BUILDING,2026-01-15,180000000\n"
    "C-2,KITCHEN REMODEL,2026-01-16,25000\n"
)


def test_csv_connector_reads_and_filters(fake_client):
    endpoint = "https://example.gov/permits.csv"
    fake_client.add("permits.csv", CSV_BODY)
    connector = CsvConnector(fake_client)
    source = connector.describe(SourceRef(
        source_id="csv:test", connector="csv", endpoint=endpoint,
        jurisdiction="Example"))

    assert source.field_map["description"] == "work_description"
    rows = list(connector.fetch_rows(source, HarvestQuery(
        keywords=["data center"], limit=10)))
    assert len(rows) == 1
    assert rows[0]["permit_number"] == "C-1"


def test_csv_connector_handles_json_array_feeds(fake_client):
    endpoint = "https://example.gov/permits.json"
    fake_client.add("permits.json", [
        {"permit_number": "J-1", "work_description": "DATA HALL FITOUT",
         "issue_date": "2026-03-01"},
    ])
    connector = CsvConnector(fake_client)
    source = connector.describe(SourceRef(
        source_id="csv:json", connector="csv", endpoint=endpoint,
        jurisdiction="Example"))
    rows = list(connector.fetch_rows(source, HarvestQuery(limit=10)))
    assert rows[0]["permit_number"] == "J-1"


# -------------------------------------------------------------------- misc


def test_to_permit_skips_rows_with_no_usable_content(fake_client):
    fake_client.add("/api/views/abcd-1234.json", SOCRATA_VIEW)
    connector = SocrataConnector(fake_client)
    source = connector.describe(socrata_source())
    assert connector.to_permit(source, {"reported_cost": "5"}) is None


def test_raw_provenance_excludes_geometry(fake_client):
    fake_client.add("/api/views/abcd-1234.json", SOCRATA_VIEW)
    connector = SocrataConnector(fake_client)
    source = connector.describe(socrata_source())
    row = dict(SOCRATA_ROWS[0], geometry={"x": 1, "y": 2})
    permit = connector.to_permit(source, row)
    assert "geometry" not in permit.raw
    assert permit.raw["permit_"] == "P-1"


def test_build_text_predicate_shape():
    predicate = build_text_predicate(["data center"], ["DESCRIPTION"], "sql92")
    assert predicate == "UPPER(DESCRIPTION) LIKE '%DATA CENTER%'"


def test_build_text_predicate_ors_across_fields_and_keywords():
    predicate = build_text_predicate(["a", "b"], ["F1", "F2"], "sql92")
    assert predicate.count(" OR ") == 3


def test_build_text_predicate_escapes_quotes_and_wildcards():
    predicate = build_text_predicate(["o'brien%"], ["F"], "soql")
    assert "''" in predicate
    # A stray % would turn into an unintended wildcard.
    assert "OBRIEN" in predicate.upper() or "O''BRIEN" in predicate.upper()


def test_build_text_predicate_returns_none_without_fields():
    assert build_text_predicate(["data center"], [], "soql") is None
    assert build_text_predicate([], ["F"], "soql") is None


def test_get_connector_rejects_unknown_names(fake_client):
    with pytest.raises(ValueError):
        get_connector("carrier-pigeon", fake_client)
