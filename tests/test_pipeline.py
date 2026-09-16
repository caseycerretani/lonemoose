"""Pipeline tests.

The guarantee worth protecting: one broken county endpoint must never
abort a nationwide run, and the failure must be recorded rather than
silently swallowed — a gap in the data has to be traceable to a source.
"""

from __future__ import annotations

import pytest

from dcpermits.discovery import Registry
from dcpermits.httpclient import HttpError
from dcpermits.models import SourceRef, TIER_UNLIKELY
from dcpermits.pipeline import Agent, RunConfig
from dcpermits.store import Store

VIEW = {
    "columns": [
        {"fieldName": "permit_", "dataTypeName": "text"},
        {"fieldName": "work_description", "dataTypeName": "text"},
        {"fieldName": "issue_date", "dataTypeName": "calendar_date"},
        {"fieldName": "reported_cost", "dataTypeName": "number"},
    ]
}

ROWS = [
    {"permit_": "P-1", "work_description": "NEW HYPERSCALE DATA CENTER SHELL",
     "issue_date": "2026-02-10T00:00:00.000", "reported_cost": "250000000"},
    {"permit_": "P-2", "work_description": "KITCHEN REMODEL",
     "issue_date": "2026-02-11T00:00:00.000", "reported_cost": "25000"},
]


def seeded_registry(path, *sources):
    registry = Registry(path)
    for source in sources:
        registry.add(source)
    registry.save()
    return registry


def good_source(name="good"):
    return SourceRef(
        source_id=f"socrata:{name}", connector="socrata",
        endpoint=f"https://{name}.example.gov/resource/abcd-1234.json",
        jurisdiction=name.title(), state="VA",
        field_map={
            "permit_number": "permit_", "description": "work_description",
            "issued_date": "issue_date", "valuation": "reported_cost",
        },
        text_fields=["work_description"], date_field="issue_date", verified=True,
    )


def make_agent(tmp_db, tmp_registry, fake_client, **overrides):
    # Point seed_path at a nonexistent file so the packaged curated sources
    # stay out of these tests; each test controls its own registry.
    config = RunConfig(db_path=tmp_db, registry_path=tmp_registry,
                       seed_path=tmp_registry.parent / "no-seed.json",
                       **overrides)
    agent = Agent(config)
    # Swap in the offline client after construction.
    agent.client = fake_client
    return agent


def test_harvest_stores_only_candidates(tmp_db, tmp_registry, fake_client):
    seeded_registry(tmp_registry, good_source())
    fake_client.add("/api/views/", VIEW).add("/resource/abcd-1234.json", ROWS)

    agent = make_agent(tmp_db, tmp_registry, fake_client)
    result = agent.harvest(since="2026-01-01")

    assert result.sources_ok == 1
    assert result.rows_fetched == 2
    # The kitchen remodel is dropped.
    assert result.candidates == 1
    assert result.stored == 1

    with Store(tmp_db) as store:
        rows = store.query(min_tier="possible")
    assert len(rows) == 1
    assert rows[0]["permit_number"] == "P-1"
    assert rows[0]["valuation"] == 250_000_000.0


def test_keep_unlikely_stores_everything(tmp_db, tmp_registry, fake_client):
    seeded_registry(tmp_registry, good_source())
    fake_client.add("/api/views/", VIEW).add("/resource/abcd-1234.json", ROWS)

    agent = make_agent(tmp_db, tmp_registry, fake_client, keep_unlikely=True)
    result = agent.harvest(since="2026-01-01")
    assert result.stored == 2
    with Store(tmp_db) as store:
        assert store.total() == 2
        assert store.counts_by_tier().get(TIER_UNLIKELY) == 1


def test_one_broken_source_does_not_abort_the_run(tmp_db, tmp_registry, fake_client):
    broken = SourceRef(
        source_id="socrata:broken", connector="socrata",
        endpoint="https://broken.example.gov/resource/dead-0000.json",
        jurisdiction="Broken", state="AZ",
        field_map={"description": "work_description"},
        text_fields=["work_description"], verified=True,
    )
    seeded_registry(tmp_registry, broken, good_source())

    fake_client.add("/api/views/", VIEW)
    fake_client.add("/resource/abcd-1234.json", ROWS)
    fake_client.add("dead-0000", HttpError("gateway timeout", status=504))

    agent = make_agent(tmp_db, tmp_registry, fake_client)
    result = agent.harvest(since="2026-01-01")

    # The healthy source still produced data.
    assert result.sources_tried == 2
    assert result.sources_ok == 1
    assert result.stored == 1
    # ...and the failure is recorded, not hidden.
    assert len(result.errors) == 1
    assert "broken" in result.errors[0]

    with Store(tmp_db) as store:
        run = store.last_runs(1)[0]
    assert run["sources_tried"] == 2
    assert run["sources_ok"] == 1
    assert "broken" in run["errors"]


def test_harvest_raises_when_no_verified_sources(tmp_db, tmp_registry, fake_client):
    seeded_registry(tmp_registry)
    agent = make_agent(tmp_db, tmp_registry, fake_client)
    with pytest.raises(RuntimeError, match="discover"):
        agent.harvest()


def test_state_filter_restricts_sources(tmp_db, tmp_registry, fake_client):
    seeded_registry(tmp_registry, good_source("va"), good_source("az"))
    # Both sources are state VA in good_source(); override one.
    registry = Registry(tmp_registry).load()
    registry.sources["socrata:az"].state = "AZ"
    registry.save()

    fake_client.add("/api/views/", VIEW).add("/resource/abcd-1234.json", ROWS)
    agent = make_agent(tmp_db, tmp_registry, fake_client, states=["AZ"])
    result = agent.harvest(since="2026-01-01")
    assert result.sources_tried == 1


def test_max_sources_caps_the_run(tmp_db, tmp_registry, fake_client):
    seeded_registry(tmp_registry, good_source("a"), good_source("b"),
                    good_source("c"))
    fake_client.add("/api/views/", VIEW).add("/resource/abcd-1234.json", ROWS)
    agent = make_agent(tmp_db, tmp_registry, fake_client, max_sources=2)
    assert agent.harvest(since="2026-01-01").sources_tried == 2


def test_repeat_harvest_is_idempotent(tmp_db, tmp_registry, fake_client):
    seeded_registry(tmp_registry, good_source())
    fake_client.add("/api/views/", VIEW).add("/resource/abcd-1234.json", ROWS)
    agent = make_agent(tmp_db, tmp_registry, fake_client)
    agent.harvest(since="2026-01-01")
    agent.harvest(since="2026-01-01")
    with Store(tmp_db) as store:
        assert store.total() == 1


def test_default_since_respects_lookback(tmp_db, tmp_registry, fake_client):
    seeded_registry(tmp_registry, good_source())
    agent = make_agent(tmp_db, tmp_registry, fake_client, lookback_days=30)
    since = agent._default_since()
    assert len(since) == 10 and since.count("-") == 2


def test_run_summary_is_informative(tmp_db, tmp_registry, fake_client):
    seeded_registry(tmp_registry, good_source())
    fake_client.add("/api/views/", VIEW).add("/resource/abcd-1234.json", ROWS)
    agent = make_agent(tmp_db, tmp_registry, fake_client)
    summary = agent.harvest(since="2026-01-01").summary()
    assert "sources ok" in summary
    assert "candidates" in summary


def test_summary_reports_both_writes_and_distinct_store_size(tmp_db, tmp_registry,
                                                             fake_client):
    """The two numbers legitimately differ and must both be visible."""
    seeded_registry(tmp_registry, good_source())
    fake_client.add("/api/views/", VIEW).add("/resource/abcd-1234.json", ROWS)
    agent = make_agent(tmp_db, tmp_registry, fake_client)
    result = agent.harvest(since="2026-01-01")
    assert result.store_total == 1
    assert "distinct in store" in result.summary()
