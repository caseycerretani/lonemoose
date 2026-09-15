"""Normalization tests — the messy-input cases that occur in real feeds."""

from __future__ import annotations

import pytest

from dcpermits.normalize import (
    clean_text,
    normalize_zip,
    parse_coord,
    parse_date,
    parse_money,
    parse_number,
)


@pytest.mark.parametrize("raw,expected", [
    ("$1,250,000.00", 1_250_000.0),
    ("1250000", 1_250_000.0),
    (1_250_000, 1_250_000.0),
    ("1.25M", 1_250_000.0),
    ("250k", 250_000.0),
    ("$0", None),
    ("", None),
    (None, None),
    ("N/A", None),
    ("-", None),
    (0, None),
    (-500, None),
    (True, None),          # bool is not a valuation
])
def test_parse_money(raw, expected):
    assert parse_money(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("2024-03-11T00:00:00.000", "2024-03-11"),
    ("2024-03-11", "2024-03-11"),
    ("03/11/2024", "2024-03-11"),
    ("3/11/24", "2024-03-11"),
    ("2024-03-11T00:00:00Z", "2024-03-11"),
    ("2024-03-11T12:00:00+05:00", "2024-03-11"),
    ("11-Mar-2024", "2024-03-11"),
    ("March 11, 2024", "2024-03-11"),
    ("", None),
    (None, None),
    ("not a date", None),
])
def test_parse_date(raw, expected):
    assert parse_date(raw) == expected


def test_parse_date_handles_arcgis_epoch_milliseconds():
    # ArcGIS returns epoch ms; treating it as seconds gives 1970.
    assert parse_date(1710115200000) == "2024-03-11"
    assert parse_date("1710115200000") == "2024-03-11"


def test_parse_date_handles_epoch_seconds():
    assert parse_date(1710115200) == "2024-03-11"


def test_parse_date_rejects_sentinel_years():
    assert parse_date("1900-01-01") == "1900-01-01"
    assert parse_date("0001-01-01") is None
    assert parse_date(-99999999999999) is None


@pytest.mark.parametrize("raw,kind,expected", [
    (41.8977, "lat", 41.8977),
    (-87.6239, "lon", -87.6239),
    ("41.8977", "lat", 41.8977),
    (0, "lat", None),           # the classic "no geocode" sentinel
    (0.0, "lon", None),
    (91.0, "lat", None),        # out of range
    (-181.0, "lon", None),
    (None, "lat", None),
    ("", "lat", None),
    ("junk", "lat", None),
])
def test_parse_coord(raw, kind, expected):
    assert parse_coord(raw, kind) == expected


def test_parse_coord_allows_longitude_beyond_latitude_range():
    assert parse_coord(-122.5, "lon") == -122.5


@pytest.mark.parametrize("raw,expected", [
    ("60601", "60601"),
    ("60601-1234", "60601"),
    ("60101-", "60101"),
    ("Chicago IL 60601", "60601"),
    ("", None),
    (None, None),
    ("abc", None),
])
def test_normalize_zip(raw, expected):
    assert normalize_zip(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("  lots   of   space  ", "lots of space"),
    ("N/A", None),
    ("null", None),
    ("unknown", None),
    ("--", None),
    ("", None),
    (None, None),
    (123, "123"),
])
def test_clean_text(raw, expected):
    assert clean_text(raw) == expected


def test_clean_text_rejects_geojson_dicts():
    """Socrata point columns are dicts; str() would yield junk."""
    assert clean_text({"type": "Point", "coordinates": [-87.6, 41.9]}) is None
    assert clean_text([1, 2]) is None


@pytest.mark.parametrize("raw,expected", [
    ("250,000", 250_000.0),
    ("250000 sf", 250_000.0),
    (0, None),
    (None, None),
])
def test_parse_number(raw, expected):
    assert parse_number(raw) == expected
