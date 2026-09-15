"""SQLite persistence.

SQLite because the whole point is an agent that can run unattended on a
schedule: results have to accumulate across runs so trends are measurable,
and the store has to survive being copied around without a server.

Two design choices worth noting:

* **Upsert on a stable uid.** The same permit reappears on every run (and
  often in two overlapping sources). Writes are idempotent, keyed on
  :attr:`Permit.uid`, and ``first_seen`` is preserved while the rest of the
  row is refreshed — so status changes are picked up without losing the
  original detection date.
* **Runs are recorded.** Every harvest writes a row to ``runs`` with what
  it touched and what failed, so a gap in the data can be traced to a
  specific failed source rather than silently looking like "no permits".
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .models import Permit, TIER_ORDER
from .normalize import utcnow_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS permits (
    uid             TEXT PRIMARY KEY,
    source_id       TEXT NOT NULL,
    jurisdiction    TEXT,
    state           TEXT,
    permit_number   TEXT,
    permit_type     TEXT,
    work_class      TEXT,
    status          TEXT,
    description     TEXT,
    address         TEXT,
    city            TEXT,
    zipcode         TEXT,
    latitude        REAL,
    longitude       REAL,
    applicant       TEXT,
    owner           TEXT,
    contractor      TEXT,
    valuation       REAL,
    square_feet     REAL,
    applied_date    TEXT,
    issued_date     TEXT,
    final_date      TEXT,
    best_date       TEXT,
    url             TEXT,
    dc_score        REAL,
    dc_tier         TEXT,
    dc_signals      TEXT,
    dc_operator     TEXT,
    dc_role         TEXT,
    raw             TEXT,
    first_seen      TEXT,
    last_seen       TEXT
);

CREATE INDEX IF NOT EXISTS idx_permits_tier ON permits(dc_tier);
CREATE INDEX IF NOT EXISTS idx_permits_state ON permits(state);
CREATE INDEX IF NOT EXISTS idx_permits_date ON permits(best_date);
CREATE INDEX IF NOT EXISTS idx_permits_operator ON permits(dc_operator);
CREATE INDEX IF NOT EXISTS idx_permits_source ON permits(source_id);

CREATE TABLE IF NOT EXISTS runs (
    run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT,
    finished_at     TEXT,
    sources_tried   INTEGER,
    sources_ok      INTEGER,
    rows_fetched    INTEGER,
    candidates      INTEGER,
    errors          TEXT
);
"""

# Columns written by upsert, in order.
_COLUMNS = [
    "uid", "source_id", "jurisdiction", "state", "permit_number", "permit_type",
    "work_class", "status", "description", "address", "city", "zipcode",
    "latitude", "longitude", "applicant", "owner", "contractor", "valuation",
    "square_feet", "applied_date", "issued_date", "final_date", "best_date",
    "url", "dc_score", "dc_tier", "dc_signals", "dc_operator", "dc_role", "raw",
]


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        # WAL keeps a long harvest from blocking a concurrent report query.
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------------------------------------------------------------- write

    def upsert_many(self, permits: Iterable[Permit]) -> int:
        """Insert or refresh permits. Returns the number written."""
        now = utcnow_iso()
        rows = []
        for permit in permits:
            values = [
                permit.uid, permit.source_id, permit.jurisdiction, permit.state,
                permit.permit_number, permit.permit_type, permit.work_class,
                permit.status, permit.description, permit.address, permit.city,
                permit.zipcode, permit.latitude, permit.longitude,
                permit.applicant, permit.owner, permit.contractor,
                permit.valuation, permit.square_feet, permit.applied_date,
                permit.issued_date, permit.final_date, permit.best_date,
                permit.url, permit.dc_score, permit.dc_tier,
                json.dumps(permit.dc_signals), permit.dc_operator, permit.dc_role,
                json.dumps(permit.raw, default=str),
            ]
            rows.append(values + [now, now])

        if not rows:
            return 0

        placeholders = ", ".join("?" for _ in _COLUMNS)
        # first_seen is kept from the original insert; everything else is
        # refreshed so status/valuation revisions are captured.
        updates = ", ".join(f"{c}=excluded.{c}" for c in _COLUMNS if c != "uid")
        sql = (
            f"INSERT INTO permits ({', '.join(_COLUMNS)}, first_seen, last_seen) "
            f"VALUES ({placeholders}, ?, ?) "
            f"ON CONFLICT(uid) DO UPDATE SET {updates}, last_seen=excluded.last_seen"
        )
        with self.conn:
            self.conn.executemany(sql, rows)
        return len(rows)

    def start_run(self) -> int:
        with self.conn:
            cursor = self.conn.execute(
                "INSERT INTO runs (started_at) VALUES (?)", (utcnow_iso(),)
            )
        return int(cursor.lastrowid)

    def finish_run(self, run_id: int, sources_tried: int, sources_ok: int,
                   rows_fetched: int, candidates: int,
                   errors: Optional[Sequence[str]] = None) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE runs SET finished_at=?, sources_tried=?, sources_ok=?, "
                "rows_fetched=?, candidates=?, errors=? WHERE run_id=?",
                (utcnow_iso(), sources_tried, sources_ok, rows_fetched, candidates,
                 json.dumps(list(errors or []))[:20000], run_id),
            )

    # ----------------------------------------------------------------- read

    def query(self, min_tier: str = "possible", states: Optional[Sequence[str]] = None,
              since: Optional[str] = None, until: Optional[str] = None,
              operator: Optional[str] = None, role: Optional[str] = None,
              min_valuation: Optional[float] = None,
              limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Query stored candidates with the usual filters."""
        allowed_tiers = _tiers_at_least(min_tier)
        clauses = [f"dc_tier IN ({', '.join('?' for _ in allowed_tiers)})"]
        params: List[Any] = list(allowed_tiers)

        if states:
            clauses.append(f"UPPER(state) IN ({', '.join('?' for _ in states)})")
            params.extend(s.upper() for s in states)
        if since:
            clauses.append("best_date >= ?")
            params.append(since)
        if until:
            clauses.append("best_date <= ?")
            params.append(until)
        if operator:
            clauses.append("dc_operator = ?")
            params.append(operator)
        if role:
            clauses.append("dc_role = ?")
            params.append(role)
        if min_valuation is not None:
            clauses.append("valuation >= ?")
            params.append(min_valuation)

        sql = (
            f"SELECT * FROM permits WHERE {' AND '.join(clauses)} "
            f"ORDER BY dc_score DESC, best_date DESC"
        )
        if limit:
            sql += " LIMIT ?"
            params.append(limit)

        with closing(self.conn.execute(sql, params)) as cursor:
            return [_row_to_dict(r) for r in cursor.fetchall()]

    def counts_by_tier(self) -> Dict[str, int]:
        with closing(self.conn.execute(
            "SELECT dc_tier, COUNT(*) AS n FROM permits GROUP BY dc_tier"
        )) as cursor:
            return {r["dc_tier"]: r["n"] for r in cursor.fetchall()}

    def total(self) -> int:
        with closing(self.conn.execute("SELECT COUNT(*) AS n FROM permits")) as cursor:
            return int(cursor.fetchone()["n"])

    def last_runs(self, limit: int = 5) -> List[Dict[str, Any]]:
        with closing(self.conn.execute(
            "SELECT * FROM runs ORDER BY run_id DESC LIMIT ?", (limit,)
        )) as cursor:
            return [dict(r) for r in cursor.fetchall()]


def _tiers_at_least(min_tier: str) -> List[str]:
    """Tiers ranked at or above ``min_tier``."""
    if min_tier not in TIER_ORDER:
        return list(TIER_ORDER)
    cutoff = TIER_ORDER.index(min_tier)
    return TIER_ORDER[: cutoff + 1]


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    data = dict(row)
    for key in ("dc_signals", "raw"):
        if isinstance(data.get(key), str):
            try:
                data[key] = json.loads(data[key])
            except json.JSONDecodeError:
                pass
    return data
