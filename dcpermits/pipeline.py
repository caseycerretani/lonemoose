"""The agent loop.

``discover -> probe -> harvest -> map -> classify -> store``

The loop is built to be run on a schedule and to be resilient: one broken
county endpoint must never abort a nationwide sweep, so per-source failures
are caught, recorded on the run row, and reported at the end. A run that
reaches 90% of sources and tells you which 10% failed is far more useful
than one that raises on the first timeout.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .classify import Classifier, ClassifierConfig
from .connectors import HarvestQuery, get_connector
from .discovery import CATALOG_QUERIES, Discovery, Registry
from .httpclient import HttpConfig, PoliteClient
from .models import Permit, SourceRef, TIER_UNLIKELY
from .store import Store

log = logging.getLogger("dcpermits.pipeline")


@dataclass
class RunConfig:
    db_path: Path = Path("data/permits.db")
    registry_path: Path = Path("data/sources.json")
    # Hand-curated sources merged on top of the discovered registry.
    # Defaults to the file shipped with the package; point it elsewhere (or
    # at a nonexistent path) to run with the registry alone.
    seed_path: Optional[Path] = None

    states: Optional[Sequence[str]] = None
    connectors: Optional[Sequence[str]] = None
    # Look-back window for the harvest. Data-center projects move fast, but
    # a year of history is what makes quarter-over-quarter trends possible.
    lookback_days: int = 365
    per_source_limit: int = 5000
    max_sources: Optional[int] = None
    full_scan: bool = False
    # Store everything harvested, including permits the classifier rejects.
    # Off by default: the rejected volume is enormous and the interesting
    # set is small.
    keep_unlikely: bool = False

    http: HttpConfig = field(default_factory=HttpConfig)
    classifier: ClassifierConfig = field(default_factory=ClassifierConfig)


@dataclass
class RunResult:
    run_id: int
    sources_tried: int = 0
    sources_ok: int = 0
    rows_fetched: int = 0
    candidates: int = 0
    stored: int = 0
    # Distinct permits in the store once the run finished. Reported
    # alongside `stored` because the two legitimately differ: `stored`
    # counts writes, and overlapping sources republish the same permit, so
    # a run can write more rows than it adds. Showing both keeps the
    # numbers reconcilable instead of looking like a miscount.
    store_total: int = 0
    errors: List[str] = field(default_factory=list)
    by_tier: Dict[str, int] = field(default_factory=dict)

    def summary(self) -> str:
        tiers = ", ".join(f"{k}={v}" for k, v in sorted(self.by_tier.items())) or "none"
        return (
            f"run {self.run_id}: {self.sources_ok}/{self.sources_tried} sources ok, "
            f"{self.rows_fetched} rows fetched, {self.candidates} candidates "
            f"({tiers}), {self.stored} written, "
            f"{self.store_total} distinct in store, {len(self.errors)} errors"
        )


class Agent:
    """Ties discovery, harvesting, classification and storage together."""

    def __init__(self, config: Optional[RunConfig] = None):
        self.config = config or RunConfig()
        self.client = PoliteClient(self.config.http)
        self.classifier = Classifier(self.config.classifier)
        self.registry = (Registry(self.config.registry_path)
                         .load()
                         .load_seed(self.config.seed_path))

    # ------------------------------------------------------------- discover

    def discover(self, queries: Optional[Sequence[str]] = None, per_query: int = 60,
                 max_probes: int = 200, include_socrata: bool = True,
                 include_arcgis: bool = True) -> Dict[str, int]:
        """Find and validate new sources, persisting the registry."""
        discovery = Discovery(self.client, self.registry)
        stats = discovery.run(
            queries=queries or CATALOG_QUERIES,
            per_query=per_query,
            max_probes=max_probes,
            include_socrata=include_socrata,
            include_arcgis=include_arcgis,
        )
        self.registry.save()
        stats["registry_total"] = len(self.registry)
        stats["registry_verified"] = len(self.registry.verified())
        return stats

    # -------------------------------------------------------------- harvest

    def harvest(self, since: Optional[str] = None) -> RunResult:
        """Harvest every selected source and store the data-center candidates."""
        since = since or self._default_since()
        sources = self.registry.select(
            states=self.config.states,
            connectors=self.config.connectors,
            only_verified=True,
        )
        if self.config.max_sources:
            sources = sources[: self.config.max_sources]

        if not sources:
            raise RuntimeError(
                "no verified sources in the registry — run `dcpermits discover` first"
            )

        keywords = self.classifier.keywords()
        query = HarvestQuery(
            keywords=keywords,
            since=since,
            limit=self.config.per_source_limit,
            full_scan=self.config.full_scan,
        )

        with Store(self.config.db_path) as store:
            run_id = store.start_run()
            result = RunResult(run_id=run_id)

            for source in sources:
                result.sources_tried += 1
                try:
                    permits = self._harvest_source(source, query)
                except Exception as exc:
                    # Deliberately broad: a single malformed feed must not
                    # end a nationwide run. The failure is recorded, not
                    # swallowed silently.
                    message = f"{source.source_id}: {type(exc).__name__}: {exc}"
                    log.warning("harvest failed for %s", message)
                    result.errors.append(message)
                    continue

                result.sources_ok += 1
                result.rows_fetched += permits["fetched"]
                keep = permits["keep"]
                result.candidates += len(keep)
                for permit in keep:
                    result.by_tier[permit.dc_tier] = result.by_tier.get(permit.dc_tier, 0) + 1
                if keep:
                    result.stored += store.upsert_many(keep)
                log.info("%s: %d rows -> %d candidates",
                         source.source_id, permits["fetched"], len(keep))

            result.store_total = store.total()
            store.finish_run(
                run_id,
                sources_tried=result.sources_tried,
                sources_ok=result.sources_ok,
                rows_fetched=result.rows_fetched,
                candidates=result.candidates,
                errors=result.errors,
            )
        return result

    def _harvest_source(self, source: SourceRef, query: HarvestQuery) -> Dict:
        connector = get_connector(source.connector, self.client)
        fetched = 0
        keep: List[Permit] = []
        for permit in connector.harvest(source, query):
            fetched += 1
            self.classifier.apply(permit)
            if self.config.keep_unlikely or permit.dc_tier != TIER_UNLIKELY:
                keep.append(permit)
        return {"fetched": fetched, "keep": keep}

    def _default_since(self) -> str:
        return (date.today() - timedelta(days=self.config.lookback_days)).isoformat()

    # ----------------------------------------------------------------- once

    def run(self, discover_first: bool = False) -> RunResult:
        """Full cycle: optional discovery, then a harvest."""
        if discover_first or not self.registry.verified():
            stats = self.discover()
            log.info("discovery: %s", stats)
        return self.harvest()
