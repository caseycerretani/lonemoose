"""Reporting tests.

The point of these is that the growth measures behave correctly on the
awkward shapes real data takes: markets with no valuation at all, one
campus generating many permits, and quarters with a zero baseline.
"""

from __future__ import annotations

import csv
import io
import json

from dcpermits import report as reporting
from dcpermits.models import (
    ROLE_EXPANSION,
    ROLE_FITOUT,
    ROLE_NEW_BUILD,
    ROLE_POWER,
    TIER_CONFIRMED,
    TIER_PROBABLE,
)


def row(**kwargs):
    base = dict(
        jurisdiction="Loudoun County", state="VA", city="Ashburn",
        permit_number="B-1", best_date="2026-02-01", valuation=100_000_000.0,
        square_feet=200_000.0, dc_tier=TIER_CONFIRMED, dc_score=95.0,
        dc_operator="Amazon Web Services", dc_role=ROLE_NEW_BUILD,
        dc_signals=["data_center"], description="NEW DATA CENTER",
        latitude=39.0, longitude=-77.5, source_id="src",
    )
    base.update(kwargs)
    return base


# ----------------------------------------------------------------- quarters


def test_quarter_of():
    assert reporting.quarter_of("2026-08-05") == "2026-Q3"
    assert reporting.quarter_of("2026-01-01") == "2026-Q1"
    assert reporting.quarter_of("2026-12-31") == "2026-Q4"
    assert reporting.quarter_of(None) is None
    assert reporting.quarter_of("") is None
    assert reporting.quarter_of("garbage") is None
    assert reporting.quarter_of("2026-13-01") is None


# ------------------------------------------------------------------ markets


def test_by_market_separates_growth_from_fitout():
    """Only new build and expansion add footprint."""
    rows = [
        row(permit_number="1", dc_role=ROLE_NEW_BUILD),
        row(permit_number="2", dc_role=ROLE_EXPANSION),
        row(permit_number="3", dc_role=ROLE_FITOUT),
        row(permit_number="4", dc_role=ROLE_POWER),
    ]
    market = reporting.by_market(rows)[0]
    assert market["permits"] == 4
    assert market["new_build"] == 1
    assert market["expansion"] == 1
    assert market["growth_permits"] == 2


def test_by_market_ranks_growth_ahead_of_raw_count():
    """One campus can emit dozens of fit-out permits; that is not growth."""
    rows = [
        row(jurisdiction="Chatty", dc_role=ROLE_FITOUT, permit_number=str(i),
            valuation=1000.0)
        for i in range(30)
    ] + [
        row(jurisdiction="Real Growth", dc_role=ROLE_NEW_BUILD,
            permit_number="x", valuation=300_000_000.0),
    ]
    markets = reporting.by_market(rows)
    assert markets[0]["market"].startswith("Real Growth")


def test_by_market_aggregates_valuation_and_operators():
    rows = [
        row(permit_number="1", valuation=100.0, dc_operator="Equinix"),
        row(permit_number="2", valuation=50.0, dc_operator="Meta"),
        row(permit_number="3", valuation=None, dc_operator="Meta"),
    ]
    market = reporting.by_market(rows)[0]
    assert market["valuation"] == 150.0
    assert market["operators"] == ["Equinix", "Meta"]


def test_by_market_tracks_latest_date():
    rows = [row(permit_number="1", best_date="2025-01-01"),
            row(permit_number="2", best_date="2026-06-01")]
    assert reporting.by_market(rows)[0]["latest_date"] == "2026-06-01"


def test_by_market_levels():
    rows = [row(jurisdiction="A", state="VA"), row(jurisdiction="B", state="VA")]
    assert len(reporting.by_market(rows, level="jurisdiction")) == 2
    assert len(reporting.by_market(rows, level="state")) == 1


def test_missing_state_does_not_crash():
    markets = reporting.by_market([row(state=None)], level="state")
    assert markets[0]["market"] == "unknown"


# ----------------------------------------------------------------- momentum


def test_momentum_compares_the_two_latest_quarters():
    rows = (
        [row(permit_number=f"a{i}", best_date="2026-01-15") for i in range(2)]
        + [row(permit_number=f"b{i}", best_date="2026-05-15") for i in range(5)]
    )
    result = reporting.momentum(rows, level="state")
    assert len(result) == 1
    entry = result[0]
    assert entry["quarter"] == "2026-Q2"
    assert entry["prior_quarter"] == "2026-Q1"
    assert entry["permits"] == 5
    assert entry["prior_permits"] == 2
    assert entry["change"] == 3
    assert entry["pct_change"] == 150.0


def test_momentum_reports_none_pct_for_a_zero_baseline():
    """Growth from nothing is 'new', not infinite."""
    rows = (
        [row(permit_number="a", state="VA", best_date="2026-01-15")]
        + [row(permit_number=f"b{i}", state="AZ", best_date="2026-05-15")
           for i in range(3)]
    )
    by_market = {m["market"]: m for m in reporting.momentum(rows, level="state")}
    assert by_market["AZ"]["prior_permits"] == 0
    assert by_market["AZ"]["pct_change"] is None


def test_momentum_drops_single_permit_noise():
    """A one-permit blip is not a trend and would drown the real movers."""
    rows = [row(permit_number="a", state="WY", best_date="2026-01-15"),
            row(permit_number="b", state="WY", best_date="2026-05-15")]
    assert reporting.momentum(rows, level="state", min_permits=2) == []


def test_momentum_needs_two_quarters():
    assert reporting.momentum([row(best_date="2026-02-01")]) == []


def test_momentum_ignores_undated_rows():
    rows = [row(permit_number=str(i), best_date=None) for i in range(5)]
    assert reporting.momentum(rows) == []


# ---------------------------------------------------------------- operators


def test_by_operator_leaderboard():
    rows = [
        row(permit_number="1", dc_operator="Meta", valuation=10.0,
            jurisdiction="A", dc_role=ROLE_NEW_BUILD),
        row(permit_number="2", dc_operator="Meta", valuation=5.0,
            jurisdiction="B", dc_role=ROLE_FITOUT),
        row(permit_number="3", dc_operator=None, valuation=99.0),
    ]
    operators = reporting.by_operator(rows)
    assert len(operators) == 1
    assert operators[0]["operator"] == "Meta"
    assert operators[0]["permits"] == 2
    assert operators[0]["growth_permits"] == 1
    assert operators[0]["valuation"] == 15.0
    assert operators[0]["markets"] == 2


# ----------------------------------------------------------------- capacity


def test_capacity_estimate_counts_only_growth_permits():
    """Fit-out permits would double-count capacity already built."""
    rows = [
        row(permit_number="1", dc_role=ROLE_NEW_BUILD, valuation=100_000_000.0),
        row(permit_number="2", dc_role=ROLE_FITOUT, valuation=900_000_000.0),
    ]
    estimate = reporting.capacity_estimate(rows, usd_per_mw=10_000_000.0)
    assert estimate["permits_counted"] == 1
    assert estimate["valuation"] == 100_000_000.0
    assert estimate["estimated_mw"] == 10.0


def test_capacity_estimate_carries_its_caveat():
    estimate = reporting.capacity_estimate([row()])
    assert "heuristic" in estimate["caveat"].lower()


def test_capacity_estimate_handles_no_valuation():
    estimate = reporting.capacity_estimate([row(valuation=None)])
    assert estimate["estimated_mw"] == 0.0


# ------------------------------------------------------------------- output


def test_build_report_structure():
    report = reporting.build_report([row()])
    for key in ("totals", "markets", "momentum", "operators",
                "capacity_estimate", "top_projects"):
        assert key in report
    assert report["totals"]["candidates"] == 1
    assert report["totals"]["date_range"] == ["2026-02-01", "2026-02-01"]


def test_markdown_renders_without_error():
    text = reporting.to_markdown(reporting.build_report([
        row(permit_number="1", best_date="2026-01-15"),
        row(permit_number="2", best_date="2026-05-15", dc_tier=TIER_PROBABLE),
    ]))
    assert "# Data-center permit activity" in text
    assert "Loudoun County" in text
    assert "Amazon Web Services" in text


def test_markdown_handles_empty_results():
    text = reporting.to_markdown(reporting.build_report([]))
    assert "0 candidate permits" in text


def test_csv_export_is_parseable_and_flattens_signals():
    output = reporting.to_csv([row(dc_signals=["data_center", "megawatt"])])
    parsed = list(csv.DictReader(io.StringIO(output)))
    assert len(parsed) == 1
    assert parsed[0]["dc_signals"] == "data_center|megawatt"
    assert parsed[0]["jurisdiction"] == "Loudoun County"


def test_geojson_export_skips_ungeocoded_rows():
    output = reporting.to_geojson([
        row(permit_number="1", latitude=39.0, longitude=-77.5),
        row(permit_number="2", latitude=None, longitude=None),
    ])
    parsed = json.loads(output)
    assert len(parsed["features"]) == 1
    # GeoJSON is [lon, lat], a classic transposition bug.
    assert parsed["features"][0]["geometry"]["coordinates"] == [-77.5, 39.0]


def test_top_projects_sorts_by_valuation():
    rows = [row(permit_number="small", valuation=1.0),
            row(permit_number="big", valuation=999.0)]
    assert reporting.top_projects(rows)[0]["permit_number"] == "big"


def test_money_formatting():
    assert reporting._money(2_500_000_000) == "$2.50B"
    assert reporting._money(250_000_000) == "$250.0M"
    assert reporting._money(25_000) == "$25K"
    assert reporting._money(None) == "—"
    assert reporting._money(0) == "—"
