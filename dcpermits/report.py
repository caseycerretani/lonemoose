"""Growth reporting.

The brief is data-center *growth*, so counting permits is not enough. Raw
permit counts are badly misleading in this domain: one campus generates
dozens of permits (shell, each building, electrical, mechanical, generator
yard), while a different jurisdiction issues a single permit for the same
scope. A market can look like it tripled purely because its clerk started
splitting trades onto separate records.

So the reports lead with measures that survive that noise:

* **New-build and expansion counts** separated from fit-out and equipment
  work, since only the former represents added footprint.
* **Declared valuation**, which tracks scope better than record counts.
* **Quarter-over-quarter deltas** per market, which is what "growth"
  actually asks for.
* **Operator attribution**, so a market's activity can be traced to who is
  building there.

An optional capacity estimate converts construction valuation to a rough
megawatt figure. It is a crude order-of-magnitude heuristic, labelled as
such everywhere it appears — never treat it as a substitute for utility
interconnection filings.
"""

from __future__ import annotations

import csv
import io
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .models import ROLE_EXPANSION, ROLE_NEW_BUILD, TIER_CONFIRMED, TIER_PROBABLE

# Rough all-in construction cost per megawatt of critical load for a
# modern facility, used only for the clearly-labelled capacity estimate.
# Permit valuations cover construction, not IT equipment, so this sits at
# the low end of published build costs.
DEFAULT_USD_PER_MW = 10_000_000.0

GROWTH_ROLES = (ROLE_NEW_BUILD, ROLE_EXPANSION)


@dataclass
class MarketRow:
    market: str
    state: Optional[str]
    permits: int = 0
    new_build: int = 0
    expansion: int = 0
    valuation: float = 0.0
    confirmed: int = 0
    probable: int = 0
    operators: List[str] = None
    latest_date: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "market": self.market,
            "state": self.state,
            "permits": self.permits,
            "new_build": self.new_build,
            "expansion": self.expansion,
            "growth_permits": self.new_build + self.expansion,
            "valuation": round(self.valuation, 2),
            "confirmed": self.confirmed,
            "probable": self.probable,
            "operators": sorted(self.operators or []),
            "latest_date": self.latest_date,
        }


def quarter_of(iso_date: Optional[str]) -> Optional[str]:
    """``2026-08-05`` -> ``2026-Q3``."""
    if not iso_date or len(iso_date) < 7:
        return None
    try:
        year = int(iso_date[:4])
        month = int(iso_date[5:7])
    except ValueError:
        return None
    if not 1 <= month <= 12:
        return None
    return f"{year}-Q{(month - 1) // 3 + 1}"


def by_market(rows: Iterable[Dict[str, Any]], level: str = "jurisdiction"
              ) -> List[Dict[str, Any]]:
    """Aggregate candidates per market.

    ``level`` is ``"jurisdiction"``, ``"state"`` or ``"city"``.
    """
    buckets: Dict[str, MarketRow] = {}
    for row in rows:
        key = _market_key(row, level)
        bucket = buckets.get(key)
        if bucket is None:
            bucket = MarketRow(market=key, state=row.get("state"), operators=[])
            buckets[key] = bucket

        bucket.permits += 1
        role = row.get("dc_role")
        if role == ROLE_NEW_BUILD:
            bucket.new_build += 1
        elif role == ROLE_EXPANSION:
            bucket.expansion += 1

        valuation = row.get("valuation")
        if isinstance(valuation, (int, float)):
            bucket.valuation += float(valuation)

        tier = row.get("dc_tier")
        if tier == TIER_CONFIRMED:
            bucket.confirmed += 1
        elif tier == TIER_PROBABLE:
            bucket.probable += 1

        operator = row.get("dc_operator")
        if operator and operator not in bucket.operators:
            bucket.operators.append(operator)

        best = row.get("best_date")
        if best and (bucket.latest_date is None or best > bucket.latest_date):
            bucket.latest_date = best

    ordered = sorted(
        buckets.values(),
        key=lambda b: (-(b.new_build + b.expansion), -b.valuation, -b.permits),
    )
    return [b.to_dict() for b in ordered]


def _market_key(row: Dict[str, Any], level: str) -> str:
    if level == "state":
        return row.get("state") or "unknown"
    if level == "city":
        return row.get("city") or row.get("jurisdiction") or "unknown"
    jurisdiction = row.get("jurisdiction") or "unknown"
    state = row.get("state")
    return f"{jurisdiction}, {state}" if state else jurisdiction


def by_quarter(rows: Iterable[Dict[str, Any]], level: str = "state"
               ) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Per-market, per-quarter counts and valuation."""
    out: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        quarter = quarter_of(row.get("best_date"))
        if not quarter:
            continue
        market = _market_key(row, level)
        cell = out[market].setdefault(
            quarter, {"permits": 0, "growth_permits": 0, "valuation": 0.0}
        )
        cell["permits"] += 1
        if row.get("dc_role") in GROWTH_ROLES:
            cell["growth_permits"] += 1
        valuation = row.get("valuation")
        if isinstance(valuation, (int, float)):
            cell["valuation"] += float(valuation)
    return {k: dict(sorted(v.items())) for k, v in out.items()}


def momentum(rows: Iterable[Dict[str, Any]], level: str = "state",
             min_permits: int = 2) -> List[Dict[str, Any]]:
    """Rank markets by change between the two most recent quarters present.

    Markets below ``min_permits`` in both quarters are dropped: a jump from
    zero to one permit is not a trend, and including it drowns the real
    movers.
    """
    quarterly = by_quarter(rows, level=level)
    all_quarters = sorted({q for market in quarterly.values() for q in market})
    if len(all_quarters) < 2:
        return []
    current, previous = all_quarters[-1], all_quarters[-2]

    results = []
    for market, quarters in quarterly.items():
        now = quarters.get(current, {})
        before = quarters.get(previous, {})
        now_count = now.get("permits", 0)
        before_count = before.get("permits", 0)
        if max(now_count, before_count) < min_permits:
            continue
        results.append({
            "market": market,
            "quarter": current,
            "prior_quarter": previous,
            "permits": now_count,
            "prior_permits": before_count,
            "change": now_count - before_count,
            "pct_change": _pct_change(before_count, now_count),
            "valuation": round(now.get("valuation", 0.0), 2),
            "prior_valuation": round(before.get("valuation", 0.0), 2),
            "growth_permits": now.get("growth_permits", 0),
        })
    return sorted(results, key=lambda r: (-r["change"], -r["valuation"]))


def _pct_change(before: float, now: float) -> Optional[float]:
    if not before:
        # Undefined rather than infinite; callers show "new" instead.
        return None
    return round((now - before) / before * 100.0, 1)


def by_operator(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Operator leaderboard."""
    counts: Counter = Counter()
    valuations: Dict[str, float] = defaultdict(float)
    markets: Dict[str, set] = defaultdict(set)
    growth: Counter = Counter()

    for row in rows:
        operator = row.get("dc_operator")
        if not operator:
            continue
        counts[operator] += 1
        valuation = row.get("valuation")
        if isinstance(valuation, (int, float)):
            valuations[operator] += float(valuation)
        market = _market_key(row, "jurisdiction")
        markets[operator].add(market)
        if row.get("dc_role") in GROWTH_ROLES:
            growth[operator] += 1

    return [
        {
            "operator": operator,
            "permits": count,
            "growth_permits": growth[operator],
            "valuation": round(valuations[operator], 2),
            "markets": len(markets[operator]),
            "market_list": sorted(markets[operator])[:10],
        }
        for operator, count in counts.most_common()
    ]


def capacity_estimate(rows: Iterable[Dict[str, Any]],
                      usd_per_mw: float = DEFAULT_USD_PER_MW) -> Dict[str, Any]:
    """Very rough MW estimate from new-build/expansion valuation.

    Only ground-up and expansion permits are counted, since fit-out and
    equipment permits would double-count the same capacity. This is an
    order-of-magnitude sanity check, not a capacity figure.
    """
    total = 0.0
    counted = 0
    for row in rows:
        if row.get("dc_role") not in GROWTH_ROLES:
            continue
        valuation = row.get("valuation")
        if isinstance(valuation, (int, float)) and valuation > 0:
            total += float(valuation)
            counted += 1
    return {
        "basis": "new_build + expansion permit valuation",
        "permits_counted": counted,
        "valuation": round(total, 2),
        "usd_per_mw": usd_per_mw,
        "estimated_mw": round(total / usd_per_mw, 1) if usd_per_mw else None,
        "caveat": (
            "Order-of-magnitude heuristic only. Permit valuations are "
            "self-declared, exclude IT equipment, and are missing entirely "
            "in many jurisdictions. Not a substitute for utility "
            "interconnection data."
        ),
    }


def top_projects(rows: Iterable[Dict[str, Any]], limit: int = 25) -> List[Dict[str, Any]]:
    """Largest individual candidates, for eyeballing the results."""
    ranked = sorted(
        rows,
        key=lambda r: (
            -(r.get("valuation") or 0),
            -(r.get("dc_score") or 0),
        ),
    )
    out = []
    for row in ranked[:limit]:
        out.append({
            "jurisdiction": row.get("jurisdiction"),
            "state": row.get("state"),
            "permit_number": row.get("permit_number"),
            "date": row.get("best_date"),
            "valuation": row.get("valuation"),
            "square_feet": row.get("square_feet"),
            "operator": row.get("dc_operator"),
            "role": row.get("dc_role"),
            "tier": row.get("dc_tier"),
            "score": row.get("dc_score"),
            "address": row.get("address"),
            "description": _truncate(row.get("description"), 220),
            "signals": row.get("dc_signals"),
        })
    return out


def build_report(rows: Sequence[Dict[str, Any]], level: str = "jurisdiction",
                 usd_per_mw: float = DEFAULT_USD_PER_MW) -> Dict[str, Any]:
    """Assemble the full report structure."""
    tiers = Counter(r.get("dc_tier") for r in rows)
    roles = Counter(r.get("dc_role") for r in rows)
    dated = [r.get("best_date") for r in rows if r.get("best_date")]
    return {
        "totals": {
            "candidates": len(rows),
            "by_tier": dict(tiers),
            "by_role": dict(roles),
            "date_range": [min(dated), max(dated)] if dated else None,
            "with_valuation": sum(1 for r in rows if r.get("valuation")),
        },
        "markets": by_market(rows, level=level),
        "momentum": momentum(rows, level="state"),
        "operators": by_operator(rows),
        "capacity_estimate": capacity_estimate(rows, usd_per_mw=usd_per_mw),
        "top_projects": top_projects(rows),
    }


# ------------------------------------------------------------------ output


def to_markdown(report: Dict[str, Any], max_rows: int = 20) -> str:
    """Human-readable summary."""
    out = io.StringIO()
    totals = report["totals"]
    out.write("# Data-center permit activity\n\n")
    out.write(f"**{totals['candidates']} candidate permits**")
    if totals.get("date_range"):
        out.write(f" from {totals['date_range'][0]} to {totals['date_range'][1]}")
    out.write("\n\n")

    tiers = totals.get("by_tier") or {}
    out.write("Confidence: " + ", ".join(
        f"{k} {v}" for k, v in sorted(tiers.items())) + "\n\n")
    roles = totals.get("by_role") or {}
    out.write("Project type: " + ", ".join(
        f"{k} {v}" for k, v in sorted(roles.items())) + "\n\n")

    out.write("## Markets\n\n")
    out.write("| Market | Growth permits | Total | Valuation | Confirmed | Operators |\n")
    out.write("|---|---:|---:|---:|---:|---|\n")
    for row in report["markets"][:max_rows]:
        operators = ", ".join(row["operators"][:3]) or "—"
        out.write(
            f"| {row['market']} | {row['growth_permits']} | {row['permits']} "
            f"| {_money(row['valuation'])} | {row['confirmed']} | {operators} |\n"
        )

    if report.get("momentum"):
        out.write("\n## Momentum (latest quarter vs prior)\n\n")
        out.write("| Market | Quarter | Permits | Prior | Change | % |\n")
        out.write("|---|---|---:|---:|---:|---:|\n")
        for row in report["momentum"][:max_rows]:
            pct = "new" if row["pct_change"] is None else f"{row['pct_change']:+.0f}%"
            out.write(
                f"| {row['market']} | {row['quarter']} | {row['permits']} "
                f"| {row['prior_permits']} | {row['change']:+d} | {pct} |\n"
            )

    if report.get("operators"):
        out.write("\n## Operators\n\n")
        out.write("| Operator | Permits | Growth | Valuation | Markets |\n")
        out.write("|---|---:|---:|---:|---:|\n")
        for row in report["operators"][:max_rows]:
            out.write(
                f"| {row['operator']} | {row['permits']} | {row['growth_permits']} "
                f"| {_money(row['valuation'])} | {row['markets']} |\n"
            )

    estimate = report.get("capacity_estimate") or {}
    if estimate.get("estimated_mw"):
        out.write(
            f"\n## Capacity estimate (heuristic)\n\n"
            f"~**{estimate['estimated_mw']:,.0f} MW** implied by "
            f"{_money(estimate['valuation'])} of new-build/expansion valuation "
            f"across {estimate['permits_counted']} permits at "
            f"{_money(estimate['usd_per_mw'])}/MW.\n\n"
            f"> {estimate['caveat']}\n"
        )

    out.write("\n## Largest projects\n\n")
    for row in report["top_projects"][:max_rows]:
        out.write(
            f"- **{_money(row['valuation'])}** — {row['jurisdiction']}"
            f"{', ' + row['state'] if row['state'] else ''}"
            f" · {row['date'] or 'no date'} · {row['tier']}/{row['role']}"
            f"{' · ' + row['operator'] if row['operator'] else ''}\n"
        )
        if row.get("description"):
            out.write(f"  - {row['description']}\n")
    return out.getvalue()


def to_csv(rows: Sequence[Dict[str, Any]]) -> str:
    """Flat CSV of candidate permits."""
    columns = [
        "jurisdiction", "state", "city", "permit_number", "permit_type",
        "status", "best_date", "applied_date", "issued_date", "valuation",
        "square_feet", "address", "zipcode", "latitude", "longitude",
        "owner", "applicant", "contractor", "dc_tier", "dc_score",
        "dc_operator", "dc_role", "dc_signals", "description", "source_id",
    ]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        record = {k: row.get(k) for k in columns}
        signals = record.get("dc_signals")
        if isinstance(signals, list):
            record["dc_signals"] = "|".join(str(s) for s in signals)
        writer.writerow(record)
    return buffer.getvalue()


def to_geojson(rows: Sequence[Dict[str, Any]]) -> str:
    """GeoJSON of the geocoded candidates, for mapping."""
    features = []
    for row in rows:
        lat, lon = row.get("latitude"), row.get("longitude")
        if lat is None or lon is None:
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                k: row.get(k) for k in (
                    "jurisdiction", "state", "permit_number", "best_date",
                    "valuation", "dc_tier", "dc_score", "dc_operator",
                    "dc_role", "description",
                )
            },
        })
    return json.dumps({"type": "FeatureCollection", "features": features}, default=str)


def _money(value: Any) -> str:
    if not isinstance(value, (int, float)) or not value:
        return "—"
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"${value / 1_000:.0f}K"
    return f"${value:,.0f}"


def _truncate(text: Optional[str], limit: int) -> Optional[str]:
    if not text:
        return None
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
