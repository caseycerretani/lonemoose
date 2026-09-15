"""Value normalization.

Permit feeds are messy in predictable ways: money arrives as "$1,250,000.00"
or "1.25M", dates in a dozen formats (including epoch milliseconds from
ArcGIS), coordinates occasionally swapped or zeroed out. These helpers are
intentionally forgiving — a permit with an unparseable valuation is still a
useful permit, so every function returns ``None`` rather than raising.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional

_MONEY_CLEAN = re.compile(r"[^0-9.\-eE]")
_MULTIPLIER = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}

_DATE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %I:%M:%S %p",
    "%m/%d/%Y",
    "%m/%d/%y",
    "%d-%b-%Y",
    "%Y/%m/%d",
    "%b %d %Y",
    "%B %d, %Y",
)

_WHITESPACE = re.compile(r"\s+")

# Plausible bounds for permit data, used to reject sentinel values.
_MIN_YEAR = 1900
_MAX_YEAR = 2100


def clean_text(value: Any) -> Optional[str]:
    """Collapse whitespace and drop placeholder strings."""
    if value is None:
        return None
    # Geometry and nested objects arrive as dicts/lists in JSON feeds.
    # Stringifying one produces junk like "{'type': 'Point', ...}" in an
    # address column, so reject them outright.
    if isinstance(value, (dict, list, tuple, set)):
        return None
    if not isinstance(value, str):
        value = str(value)
    value = _WHITESPACE.sub(" ", value).strip()
    if not value:
        return None
    # Feeds use these to mean "empty"; keeping them pollutes every report.
    if value.lower() in {"n/a", "na", "none", "null", "unknown", "-", "--", "tbd", "."}:
        return None
    return value


def parse_money(value: Any) -> Optional[float]:
    """Parse a valuation into float USD.

    Handles ``"$1,250,000"``, ``"1.25M"``, ``"250k"`` and plain numerics.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        amount = float(value)
        return amount if amount > 0 else None

    text = str(value).strip().lower()
    if not text:
        return None

    multiplier = 1
    # Only treat a trailing k/m/b as a multiplier, never a letter buried
    # mid-string (that is far more likely to be a stray code).
    if text and text[-1] in _MULTIPLIER and any(c.isdigit() for c in text):
        multiplier = _MULTIPLIER[text[-1]]
        text = text[:-1]

    cleaned = _MONEY_CLEAN.sub("", text)
    if not cleaned or cleaned in {"-", ".", "-."}:
        return None
    try:
        amount = float(cleaned) * multiplier
    except ValueError:
        return None
    if amount <= 0:
        return None
    return amount


def parse_number(value: Any) -> Optional[float]:
    """Parse a plain quantity such as square footage."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    cleaned = _MONEY_CLEAN.sub("", str(value))
    if not cleaned or cleaned in {"-", ".", "-."}:
        return None
    try:
        number = float(cleaned)
    except ValueError:
        return None
    return number if number > 0 else None


def parse_date(value: Any) -> Optional[str]:
    """Parse a date into an ISO-8601 ``YYYY-MM-DD`` string.

    ArcGIS layers return epoch milliseconds, Socrata returns ISO strings,
    and CSV exports return whatever the clerk's spreadsheet produced.
    """
    if value is None or isinstance(value, bool):
        return None

    # Epoch handling: ArcGIS uses milliseconds, some feeds use seconds.
    if isinstance(value, (int, float)):
        return _from_epoch(float(value))

    text = str(value).strip()
    if not text:
        return None

    # A bare numeric string is still an epoch.
    if re.fullmatch(r"-?\d{9,14}", text):
        return _from_epoch(float(text))

    # Trailing "Z" is valid ISO but not accepted by our format list.
    candidate = text[:-1] if text.endswith("Z") else text
    # Strip a numeric UTC offset; we normalize to the date only anyway.
    # Guarded on the presence of a time, because "-2024" in a date like
    # "11-Mar-2024" is otherwise indistinguishable from a "-2024" offset
    # and would leave "11-Mar" behind.
    if ":" in candidate:
        candidate = re.sub(r"([+-]\d{2}:?\d{2})$", "", candidate).strip()

    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(candidate, fmt)
        except ValueError:
            continue
        if _MIN_YEAR <= parsed.year <= _MAX_YEAR:
            return parsed.date().isoformat()
        return None
    return None


def _from_epoch(raw: float) -> Optional[str]:
    # Distinguish seconds from milliseconds by magnitude. 1e11 seconds is
    # year 5138, so anything larger is milliseconds.
    seconds = raw / 1000.0 if abs(raw) > 1e11 else raw
    try:
        parsed = datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    if not _MIN_YEAR <= parsed.year <= _MAX_YEAR:
        return None
    return parsed.date().isoformat()


def parse_coord(value: Any, kind: str) -> Optional[float]:
    """Parse a latitude or longitude, rejecting out-of-range sentinels."""
    number = None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
    elif value is not None:
        try:
            number = float(str(value).strip())
        except (TypeError, ValueError):
            return None
    if number is None:
        return None
    # 0,0 is the classic "no geocode" sentinel in permit feeds.
    if number == 0:
        return None
    limit = 90.0 if kind == "lat" else 180.0
    if abs(number) > limit:
        return None
    return number


def normalize_zip(value: Any) -> Optional[str]:
    """Return a 5-digit ZIP, discarding ZIP+4 and junk."""
    text = clean_text(value)
    if not text:
        return None
    match = re.search(r"\b(\d{5})(?:-\d{4})?\b", text)
    return match.group(1) if match else None


def utcnow_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
