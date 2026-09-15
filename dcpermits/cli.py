"""Command-line interface.

    dcpermits discover                 find and validate permit sources
    dcpermits harvest                  fetch + classify + store
    dcpermits report                   growth analysis from the store
    dcpermits sources                  inspect the source registry
    dcpermits inspect <endpoint>       debug field mapping for one endpoint
    dcpermits classify "<text>"        explain a classification decision
    dcpermits run                      discover (if needed) then harvest
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Optional, Sequence

from . import report as reporting
from .classify import Classifier
from .connectors import get_connector
from .discovery import CATALOG_QUERIES, Registry
from .httpclient import HttpConfig, PoliteClient
from .models import Permit, SourceRef
from .pipeline import Agent, RunConfig
from .schema_map import explain_mapping
from .store import Store


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    if not getattr(args, "command", None):
        parser.print_help()
        return 1
    return int(args.handler(args) or 0)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dcpermits",
        description="Harvest public building permits nationwide and surface "
                    "data-center construction activity.",
    )
    parser.add_argument("--log-level", default="info",
                        choices=["debug", "info", "warning", "error"])
    parser.add_argument("--db", type=Path, default=Path("data/permits.db"),
                        help="SQLite store path")
    parser.add_argument("--registry", type=Path, default=Path("data/sources.json"),
                        help="source registry path")
    parser.add_argument("--seed", type=Path, default=None,
                        help="hand-curated sources merged over the registry "
                             "(default: the file shipped with the package)")
    parser.add_argument("--delay", type=float, default=1.0,
                        help="minimum seconds between requests to the same host")
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--ignore-robots", action="store_true",
                        help="do not consult robots.txt (use only where you have "
                             "confirmed the data policy permits it)")

    sub = parser.add_subparsers(dest="command")

    # ------------------------------------------------------------- discover
    discover = sub.add_parser("discover", help="find and validate permit sources")
    discover.add_argument("--query", action="append", default=None,
                          help="catalog search term (repeatable)")
    discover.add_argument("--per-query", type=int, default=60)
    discover.add_argument("--max-probes", type=int, default=200,
                          help="cap on endpoints probed this run")
    discover.add_argument("--socrata-only", action="store_true")
    discover.add_argument("--arcgis-only", action="store_true")
    discover.set_defaults(handler=_cmd_discover)

    # -------------------------------------------------------------- harvest
    harvest = sub.add_parser("harvest", help="fetch, classify and store permits")
    _add_harvest_args(harvest)
    harvest.set_defaults(handler=_cmd_harvest)

    # ------------------------------------------------------------------ run
    run = sub.add_parser("run", help="discover if needed, then harvest")
    _add_harvest_args(run)
    run.add_argument("--discover", action="store_true",
                     help="force a discovery pass first")
    run.set_defaults(handler=_cmd_run)

    # --------------------------------------------------------------- report
    report = sub.add_parser("report", help="growth analysis from the store")
    report.add_argument("--format", default="markdown",
                        choices=["markdown", "json", "csv", "geojson"])
    report.add_argument("--level", default="jurisdiction",
                        choices=["jurisdiction", "state", "city"])
    report.add_argument("--min-tier", default="probable",
                        choices=["confirmed", "probable", "possible", "unlikely"])
    report.add_argument("--state", action="append", default=None,
                        help="filter by state code (repeatable)")
    report.add_argument("--since", default=None, help="ISO date lower bound")
    report.add_argument("--until", default=None, help="ISO date upper bound")
    report.add_argument("--operator", default=None)
    report.add_argument("--role", default=None,
                        choices=["new_build", "expansion", "fitout",
                                 "power_infrastructure", "equipment", "other"])
    report.add_argument("--min-valuation", type=float, default=None)
    report.add_argument("--usd-per-mw", type=float,
                        default=reporting.DEFAULT_USD_PER_MW)
    report.add_argument("--limit", type=int, default=None)
    report.add_argument("--out", type=Path, default=None, help="write to a file")
    report.set_defaults(handler=_cmd_report)

    # -------------------------------------------------------------- sources
    sources = sub.add_parser("sources", help="inspect the source registry")
    sources.add_argument("--state", action="append", default=None)
    sources.add_argument("--connector", action="append", default=None)
    sources.add_argument("--all", action="store_true",
                         help="include unverified sources")
    sources.add_argument("--format", default="table", choices=["table", "json"])
    sources.set_defaults(handler=_cmd_sources)

    # -------------------------------------------------------------- inspect
    inspect = sub.add_parser(
        "inspect", help="show the derived field mapping for one endpoint")
    inspect.add_argument("endpoint")
    inspect.add_argument("--connector", default="arcgis",
                         choices=["arcgis", "socrata", "csv"])
    inspect.add_argument("--sample", type=int, default=0,
                         help="also fetch and classify N rows")
    inspect.add_argument("--explain", action="store_true",
                         help="show the ranked candidates considered for each "
                              "canonical field, to debug a bad mapping")
    inspect.set_defaults(handler=_cmd_inspect)

    # ------------------------------------------------------------- classify
    classify = sub.add_parser(
        "classify", help="explain how a description would be classified")
    classify.add_argument("text", nargs="+")
    classify.add_argument("--valuation", type=float, default=None)
    classify.add_argument("--sqft", type=float, default=None)
    classify.set_defaults(handler=_cmd_classify)

    # --------------------------------------------------------------- status
    status = sub.add_parser("status", help="store and run statistics")
    status.set_defaults(handler=_cmd_status)

    return parser


def _add_harvest_args(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("--state", action="append", default=None,
                     help="restrict to state code (repeatable)")
    sub.add_argument("--connector", action="append", default=None,
                     choices=["socrata", "arcgis", "csv"])
    sub.add_argument("--lookback-days", type=int, default=365)
    sub.add_argument("--since", default=None,
                     help="ISO date; overrides --lookback-days")
    sub.add_argument("--per-source-limit", type=int, default=5000)
    sub.add_argument("--max-sources", type=int, default=None)
    sub.add_argument("--full-scan", action="store_true",
                     help="skip server-side keyword filtering (slow, higher recall)")
    sub.add_argument("--keep-unlikely", action="store_true",
                     help="store non-candidates too (large)")


# ----------------------------------------------------------------- handlers


def _http_config(args) -> HttpConfig:
    return HttpConfig(
        timeout=args.timeout,
        per_host_delay=args.delay,
        respect_robots=not args.ignore_robots,
    )


def _agent(args) -> Agent:
    config = RunConfig(
        db_path=args.db,
        registry_path=args.registry,
        seed_path=getattr(args, "seed", None),
        states=getattr(args, "state", None),
        connectors=getattr(args, "connector", None),
        lookback_days=getattr(args, "lookback_days", 365),
        per_source_limit=getattr(args, "per_source_limit", 5000),
        max_sources=getattr(args, "max_sources", None),
        full_scan=getattr(args, "full_scan", False),
        keep_unlikely=getattr(args, "keep_unlikely", False),
        http=_http_config(args),
    )
    return Agent(config)


def _cmd_discover(args) -> int:
    agent = _agent(args)
    stats = agent.discover(
        queries=args.query or CATALOG_QUERIES,
        per_query=args.per_query,
        max_probes=args.max_probes,
        include_socrata=not args.arcgis_only,
        include_arcgis=not args.socrata_only,
    )
    print(json.dumps(stats, indent=2))
    print(f"\nregistry: {args.registry}", file=sys.stderr)
    return 0


def _cmd_harvest(args) -> int:
    agent = _agent(args)
    result = agent.harvest(since=args.since)
    print(result.summary())
    if result.errors:
        print(f"\nfirst {min(10, len(result.errors))} errors:", file=sys.stderr)
        for message in result.errors[:10]:
            print(f"  {message}", file=sys.stderr)
    # A run where nothing succeeded is a failure, not a quiet success.
    return 0 if result.sources_ok else 1


def _cmd_run(args) -> int:
    agent = _agent(args)
    result = agent.run(discover_first=args.discover)
    print(result.summary())
    return 0 if result.sources_ok else 1


def _cmd_report(args) -> int:
    with Store(args.db) as store:
        rows = store.query(
            min_tier=args.min_tier,
            states=args.state,
            since=args.since,
            until=args.until,
            operator=args.operator,
            role=args.role,
            min_valuation=args.min_valuation,
            limit=args.limit,
        )

    if args.format == "csv":
        output = reporting.to_csv(rows)
    elif args.format == "geojson":
        output = reporting.to_geojson(rows)
    else:
        built = reporting.build_report(rows, level=args.level,
                                       usd_per_mw=args.usd_per_mw)
        output = (json.dumps(built, indent=2, default=str)
                  if args.format == "json" else reporting.to_markdown(built))

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output, encoding="utf-8")
        print(f"wrote {args.out} ({len(rows)} candidates)", file=sys.stderr)
    else:
        print(output)
    return 0


def _cmd_sources(args) -> int:
    registry = Registry(args.registry).load().load_seed(args.seed)
    selected = registry.select(
        states=args.state, connectors=args.connector, only_verified=not args.all
    )
    if args.format == "json":
        print(json.dumps([s.to_dict() for s in selected], indent=2))
        return 0

    if not selected:
        print("no sources — run `dcpermits discover` first", file=sys.stderr)
        return 1

    print(f"{'STATE':<6} {'CONNECTOR':<9} {'JURISDICTION':<32} ENDPOINT")
    for source in selected:
        print(f"{(source.state or '--'):<6} {source.connector:<9} "
              f"{source.jurisdiction[:32]:<32} {source.endpoint[:70]}")
    print(f"\n{len(selected)} sources "
          f"({len(registry.verified())} verified of {len(registry)} total)",
          file=sys.stderr)
    return 0


def _cmd_inspect(args) -> int:
    client = PoliteClient(_http_config(args))
    connector = get_connector(args.connector, client)
    source = SourceRef(
        source_id="inspect",
        connector=args.connector,
        endpoint=args.endpoint,
        jurisdiction="inspect",
    )
    described = connector.describe(source)

    print(f"verified:    {described.verified}")
    print(f"date field:  {described.date_field}")
    print(f"text fields: {described.text_fields}")
    print("\nfield map:")
    for canonical, provider in sorted(described.field_map.items()):
        print(f"  {canonical:<16} <- {provider}")

    if described.address_parts:
        print(f"\naddress assembled from: {' + '.join(described.address_parts)}")

    missing = [f for f in ("description", "permit_number", "issued_date", "valuation")
               if f not in described.field_map]
    if missing:
        print(f"\nunmapped key fields: {', '.join(missing)}")

    if args.explain:
        # Show what else was considered and how it scored — the quickest way
        # to see why a field was claimed by the wrong canonical name.
        provider_fields = sorted(
            set(described.field_map.values()) | set(described.text_fields)
            | set(described.address_parts)
        )
        print("\nranked candidates per field:")
        for canonical, ranked in explain_mapping(provider_fields).items():
            if ranked:
                shown = ", ".join(f"{name} ({score})" for name, score in ranked)
                print(f"  {canonical:<16} {shown}")

    if args.sample:
        from .connectors import HarvestQuery
        classifier = Classifier()
        query = HarvestQuery(keywords=(), limit=args.sample, full_scan=True)
        print(f"\nsample of {args.sample} rows:")
        for permit in connector.harvest(described, query):
            classifier.apply(permit)
            print(f"  [{permit.dc_tier:<9} {permit.dc_score:>6.1f}] "
                  f"{(permit.permit_number or '?'):<14} "
                  f"{(permit.description or '')[:90]}")
    return 0


def _cmd_classify(args) -> int:
    classifier = Classifier()
    permit = Permit(
        source_id="cli",
        jurisdiction="cli",
        description=" ".join(args.text),
        valuation=args.valuation,
        square_feet=args.sqft,
    )
    result = classifier.classify(permit)
    print(json.dumps({
        "score": result.score,
        "tier": result.tier,
        "role": result.role,
        "operator": result.operator,
        "operator_match": result.operator_match,
        "vetoed": result.vetoed,
        "signals": result.signals,
    }, indent=2))
    return 0


def _cmd_status(args) -> int:
    registry = Registry(args.registry).load().load_seed(args.seed)
    if not Path(args.db).exists():
        print(f"no store at {args.db}")
        print(f"registry: {len(registry.verified())} verified of {len(registry)}")
        return 0
    with Store(args.db) as store:
        print(f"store:    {args.db}")
        print(f"permits:  {store.total()}")
        print(f"by tier:  {store.counts_by_tier()}")
        print(f"registry: {len(registry.verified())} verified of {len(registry)}")
        runs = store.last_runs(5)
        if runs:
            print("\nrecent runs:")
            for run in runs:
                errors = json.loads(run.get("errors") or "[]")
                print(f"  #{run['run_id']} {run.get('started_at')} "
                      f"sources={run.get('sources_ok')}/{run.get('sources_tried')} "
                      f"rows={run.get('rows_fetched')} "
                      f"candidates={run.get('candidates')} errors={len(errors)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
