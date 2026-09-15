"""Schema-mapping tests.

The field lists below are real schemas captured from live endpoints. They
are the whole reason this module exists: the same logical column is named
``work_description`` in Chicago and ``submission_type_name`` elsewhere, and
a mapper that works on tidy invented names is worthless.
"""

from __future__ import annotations

from dcpermits.schema_map import (
    find_address_parts,
    map_fields,
    pick_text_fields,
    score_field,
)

# Captured from https://data.cityofchicago.org/resource/ydr8-5enu.json
CHICAGO = [
    {"name": "id", "type": "text"},
    {"name": "permit_", "type": "text"},
    {"name": "permit_status", "type": "text"},
    {"name": "permit_milestone", "type": "text"},
    {"name": "permit_type", "type": "text"},
    {"name": "review_type", "type": "text"},
    {"name": "application_start_date", "type": "calendar_date"},
    {"name": "issue_date", "type": "calendar_date"},
    {"name": "processing_time", "type": "number"},
    {"name": "street_number", "type": "text"},
    {"name": "street_direction", "type": "text"},
    {"name": "street_name", "type": "text"},
    {"name": "work_type", "type": "text"},
    {"name": "work_description", "type": "text"},
    {"name": "building_fee_paid", "type": "number"},
    {"name": "total_fee", "type": "number"},
    {"name": "reported_cost", "type": "number"},
    {"name": "contact_10_type", "type": "text"},
    {"name": "contact_10_zipcode", "type": "text"},
    {"name": "latitude", "type": "number"},
    {"name": "longitude", "type": "number"},
    {"name": "location", "type": "point"},
]

# Captured from an ArcGIS FeatureServer permit layer.
ARCGIS_LAYER = [
    {"name": "OBJECTID", "type": "esriFieldTypeOID"},
    {"name": "submission_number", "type": "esriFieldTypeString"},
    {"name": "name", "type": "esriFieldTypeString"},
    {"name": "location", "type": "esriFieldTypeString"},
    {"name": "location_lat", "type": "esriFieldTypeDouble"},
    {"name": "location_lng", "type": "esriFieldTypeDouble"},
    {"name": "submission_type_name", "type": "esriFieldTypeString"},
    {"name": "status", "type": "esriFieldTypeString"},
    {"name": "created_at", "type": "esriFieldTypeDate"},
    {"name": "Project_Valuation", "type": "esriFieldTypeDouble"},
]


def test_chicago_maps_core_fields():
    mapping = map_fields(CHICAGO)
    assert mapping["description"] == "work_description"
    assert mapping["permit_number"] == "permit_"
    assert mapping["issued_date"] == "issue_date"
    assert mapping["applied_date"] == "application_start_date"
    assert mapping["valuation"] == "reported_cost"
    assert mapping["status"] == "permit_status"
    assert mapping["latitude"] == "latitude"
    assert mapping["longitude"] == "longitude"


def test_fee_is_never_mistaken_for_valuation():
    """Conflating fee with valuation understates projects ~1000x."""
    mapping = map_fields(CHICAGO)
    assert mapping["valuation"] not in ("total_fee", "building_fee_paid")


def test_geometry_column_is_not_mapped_to_address():
    """Socrata's ``location`` is a GeoJSON dict, not a street address."""
    mapping = map_fields(CHICAGO)
    assert mapping.get("address") != "location"


def test_contact_block_is_not_mistaken_for_site_details():
    """``contact_10_zipcode`` is the applicant's mailing ZIP, not the site."""
    mapping = map_fields(CHICAGO)
    assert mapping.get("zipcode") != "contact_10_zipcode"
    assert mapping.get("permit_type") != "contact_10_type"


def test_address_components_are_found_when_there_is_no_address_column():
    mapping = map_fields(CHICAGO)
    assert "address" not in mapping
    parts = find_address_parts(CHICAGO)
    assert parts == ["street_number", "street_direction", "street_name"]


def test_address_component_is_not_mapped_as_the_whole_address():
    # A bare "N" from street_direction is a useless address.
    mapping = map_fields(CHICAGO)
    assert mapping.get("address") not in ("street_direction", "street_name",
                                          "street_number")


def test_arcgis_layer_maps_valuation_and_coords():
    mapping = map_fields(ARCGIS_LAYER)
    assert mapping["valuation"] == "Project_Valuation"
    assert mapping["latitude"] == "location_lat"
    assert mapping["longitude"] == "location_lng"
    assert mapping["permit_number"] == "submission_number"
    assert mapping["status"] == "status"


def test_each_provider_field_is_claimed_once():
    mapping = map_fields(CHICAGO)
    assert len(set(mapping.values())) == len(mapping)


def test_issue_date_not_double_mapped():
    """A single date column must not satisfy both date fields."""
    mapping = map_fields([
        {"name": "permit_number", "type": "text"},
        {"name": "issue_date", "type": "calendar_date"},
        {"name": "description", "type": "text"},
    ])
    assert mapping.get("issued_date") == "issue_date"
    assert mapping.get("applied_date") != "issue_date"


def test_issued_by_is_not_a_date():
    assert score_field("issued_date", "ISSUED_BY", "esriFieldTypeString") == 0.0


def test_owner_phone_is_not_the_owner():
    assert score_field("owner", "owner_phone", "text") == 0.0


def test_lot_area_is_not_building_area():
    assert score_field("square_feet", "lot_area", "number") == 0.0


def test_type_mismatch_is_penalised_not_fatal():
    """Numbers shipped as text are common and must still map."""
    as_number = score_field("valuation", "valuation", "number")
    as_text = score_field("valuation", "valuation", "text")
    assert as_text > 0
    assert as_number > as_text


def test_case_and_separator_insensitivity():
    for name in ("Permit Number", "permit-number", "PERMIT_NUMBER", "PermitNumber"):
        assert score_field("permit_number", name) > 0


def test_pick_text_fields_finds_descriptive_columns():
    fields = pick_text_fields(CHICAGO)
    assert "work_description" in fields
    # Geometry and numeric columns are not searchable text.
    assert "location" not in fields
    assert "reported_cost" not in fields


def test_unknown_schema_maps_nothing_rather_than_guessing():
    mapping = map_fields([
        {"name": "Field1", "type": "text"},
        {"name": "Field14", "type": "text"},
        {"name": "xyz_9", "type": "text"},
    ])
    assert mapping == {}


def test_empty_schema_is_safe():
    assert map_fields([]) == {}
    assert find_address_parts([]) == []
    assert pick_text_fields([]) == []


def test_plain_string_field_lists_are_accepted():
    mapping = map_fields(["permit_number", "work_description", "issue_date"])
    assert mapping["description"] == "work_description"


def test_mapping_is_deterministic():
    assert map_fields(CHICAGO) == map_fields(CHICAGO)
