"""Store tests: identity, idempotent upsert, and tier filtering."""

from __future__ import annotations

from dcpermits.models import (
    ROLE_NEW_BUILD,
    TIER_CONFIRMED,
    TIER_POSSIBLE,
    TIER_PROBABLE,
    TIER_UNLIKELY,
    Permit,
)
from dcpermits.store import Store, _tiers_at_least


def make_permit(**kwargs):
    defaults = dict(
        source_id="src-a",
        jurisdiction="Loudoun County",
        state="VA",
        permit_number="B-1",
        description="NEW DATA CENTER",
        valuation=200_000_000.0,
        issued_date="2026-02-01",
        dc_score=95.0,
        dc_tier=TIER_CONFIRMED,
        dc_role=ROLE_NEW_BUILD,
        dc_signals=["data_center"],
    )
    defaults.update(kwargs)
    return Permit(**defaults)


# ------------------------------------------------------------------ identity


def test_uid_is_stable_across_runs():
    assert make_permit().uid == make_permit().uid


def test_uid_keys_on_permit_number_not_volatile_row_id():
    """Feeds reshuffle internal row ids; the permit number is the identity."""
    a = make_permit(raw={"row_id": 1})
    b = make_permit(raw={"row_id": 99999})
    assert a.uid == b.uid


def test_uid_differs_across_sources():
    assert make_permit(source_id="src-a").uid != make_permit(source_id="src-b").uid


def test_uid_falls_back_to_content_hash_without_permit_number():
    a = make_permit(permit_number=None, address="1 A ST")
    b = make_permit(permit_number=None, address="2 B ST")
    assert a.uid != b.uid
    assert a.uid == make_permit(permit_number=None, address="1 A ST").uid


def test_best_date_prefers_issued_then_applied():
    assert make_permit(issued_date="2026-02-01",
                       applied_date="2025-01-01").best_date == "2026-02-01"
    assert make_permit(issued_date=None,
                       applied_date="2025-01-01").best_date == "2025-01-01"


# -------------------------------------------------------------------- upsert


def test_upsert_is_idempotent(tmp_db):
    with Store(tmp_db) as store:
        store.upsert_many([make_permit()])
        store.upsert_many([make_permit()])
        assert store.total() == 1


def test_upsert_refreshes_fields_but_keeps_first_seen(tmp_db):
    with Store(tmp_db) as store:
        store.upsert_many([make_permit(status="Applied")])
        first = store.query(min_tier=TIER_CONFIRMED)[0]

        # Same permit, later revision with an updated status and valuation.
        store.upsert_many([make_permit(status="Issued", valuation=260_000_000.0)])
        second = store.query(min_tier=TIER_CONFIRMED)[0]

        assert store.total() == 1
        assert second["status"] == "Issued"
        assert second["valuation"] == 260_000_000.0
        assert second["first_seen"] == first["first_seen"]


def test_upsert_of_empty_iterable_is_a_noop(tmp_db):
    with Store(tmp_db) as store:
        assert store.upsert_many([]) == 0
        assert store.total() == 0


def test_signals_and_raw_round_trip_as_json(tmp_db):
    with Store(tmp_db) as store:
        store.upsert_many([make_permit(
            dc_signals=["data_center", "operator:vadata"],
            raw={"a": 1, "b": "two"})])
        row = store.query(min_tier=TIER_CONFIRMED)[0]
        assert row["dc_signals"] == ["data_center", "operator:vadata"]
        assert row["raw"]["b"] == "two"


# ------------------------------------------------------------------ querying


def test_tiers_at_least_is_inclusive_downward():
    assert _tiers_at_least(TIER_CONFIRMED) == [TIER_CONFIRMED]
    assert _tiers_at_least(TIER_PROBABLE) == [TIER_CONFIRMED, TIER_PROBABLE]
    assert TIER_UNLIKELY in _tiers_at_least(TIER_UNLIKELY)


def test_query_filters_by_tier(tmp_db):
    with Store(tmp_db) as store:
        store.upsert_many([
            make_permit(permit_number="B-1", dc_tier=TIER_CONFIRMED),
            make_permit(permit_number="B-2", dc_tier=TIER_POSSIBLE, dc_score=25),
            make_permit(permit_number="B-3", dc_tier=TIER_UNLIKELY, dc_score=1),
        ])
        assert len(store.query(min_tier=TIER_CONFIRMED)) == 1
        assert len(store.query(min_tier=TIER_POSSIBLE)) == 2
        assert len(store.query(min_tier=TIER_UNLIKELY)) == 3


def test_query_filters_by_state_date_and_valuation(tmp_db):
    with Store(tmp_db) as store:
        store.upsert_many([
            make_permit(permit_number="B-1", state="VA", issued_date="2026-02-01",
                        valuation=200_000_000.0),
            make_permit(permit_number="B-2", state="AZ", issued_date="2024-01-01",
                        valuation=1_000_000.0),
        ])
        assert len(store.query(states=["VA"])) == 1
        assert len(store.query(states=["va"])) == 1        # case-insensitive
        assert len(store.query(since="2026-01-01")) == 1
        assert len(store.query(until="2025-01-01")) == 1
        assert len(store.query(min_valuation=100_000_000)) == 1


def test_query_orders_by_score_descending(tmp_db):
    with Store(tmp_db) as store:
        store.upsert_many([
            make_permit(permit_number="low", dc_score=45.0, dc_tier=TIER_PROBABLE),
            make_permit(permit_number="high", dc_score=120.0),
        ])
        rows = store.query(min_tier=TIER_PROBABLE)
        assert [r["permit_number"] for r in rows] == ["high", "low"]


def test_counts_by_tier(tmp_db):
    with Store(tmp_db) as store:
        store.upsert_many([
            make_permit(permit_number="B-1", dc_tier=TIER_CONFIRMED),
            make_permit(permit_number="B-2", dc_tier=TIER_CONFIRMED),
            make_permit(permit_number="B-3", dc_tier=TIER_PROBABLE),
        ])
        assert store.counts_by_tier() == {TIER_CONFIRMED: 2, TIER_PROBABLE: 1}


# ----------------------------------------------------------------- run rows


def test_run_lifecycle_is_recorded(tmp_db):
    """A gap in the data must be traceable to a failed source."""
    with Store(tmp_db) as store:
        run_id = store.start_run()
        store.finish_run(run_id, sources_tried=10, sources_ok=8,
                         rows_fetched=500, candidates=12,
                         errors=["src-x: HttpError: 503"])
        run = store.last_runs(1)[0]
        assert run["sources_tried"] == 10
        assert run["sources_ok"] == 8
        assert run["candidates"] == 12
        assert "src-x" in run["errors"]
        assert run["finished_at"]


def test_store_reopens_existing_database(tmp_db):
    with Store(tmp_db) as store:
        store.upsert_many([make_permit()])
    with Store(tmp_db) as store:
        assert store.total() == 1
