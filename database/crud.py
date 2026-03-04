"""
database/crud.py
----------------
All database read/write operations (Create, Read, Update, Delete).

Every function accepts an open SQLAlchemy Session as its first argument
so callers control transaction boundaries.  Functions are intentionally
kept small and single-purpose to make them easy to test and reuse.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from database.models import MonthlySummary, RawData


# ===========================================================================
# RawData CRUD
# ===========================================================================


def create_raw_entry(
    db: Session,
    *,
    device: str,
    value: float,
    timestamp: datetime,
) -> RawData:
    """Insert a single submeter ping into raw_data and return it."""
    entry = RawData(device=device, value=value, timestamp=timestamp)
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def get_recent_raw_data(db: Session, *, limit: int = 100) -> list[RawData]:
    """Return the most-recent `limit` raw records ordered newest-first."""
    return (
        db.query(RawData)
        .order_by(RawData.timestamp.desc())
        .limit(limit)
        .all()
    )


def get_distinct_devices(db: Session) -> list[str]:
    """Return a sorted list of all unique device names in raw_data."""
    rows = db.query(RawData.device).distinct().all()
    return sorted(r.device for r in rows)


def get_last_raw_entry_for_month(
    db: Session,
    *,
    device: str,
    year: int,
    month: int,
) -> Optional[RawData]:
    """
    Return the single raw record with the highest timestamp for `device`
    within the specified calendar month.  Returns None if no data exists.
    """
    # Build naive UTC boundaries for the target month
    from calendar import monthrange

    _, last_day = monthrange(year, month)
    start = datetime(year, month, 1, 0, 0, 0)
    end = datetime(year, month, last_day, 23, 59, 59, 999999)

    return (
        db.query(RawData)
        .filter(
            RawData.device == device,
            RawData.timestamp >= start,
            RawData.timestamp <= end,
        )
        .order_by(RawData.timestamp.desc())
        .first()
    )


def delete_raw_data_for_month(
    db: Session,
    *,
    device: str,
    year: int,
    month: int,
) -> int:
    """
    Delete all raw records for `device` in the given calendar month.
    Returns the number of rows deleted.
    """
    from calendar import monthrange

    _, last_day = monthrange(year, month)
    start = datetime(year, month, 1, 0, 0, 0)
    end = datetime(year, month, last_day, 23, 59, 59, 999999)

    deleted = (
        db.query(RawData)
        .filter(
            RawData.device == device,
            RawData.timestamp >= start,
            RawData.timestamp <= end,
        )
        .delete(synchronize_session=False)
    )
    db.commit()
    return deleted


# ===========================================================================
# MonthlySummary CRUD
# ===========================================================================


def create_monthly_summary(
    db: Session,
    *,
    device: str,
    month_year: str,
    last_value: float,
    usage_difference: float,
) -> MonthlySummary:
    """
    Insert (or replace) a monthly summary record.

    If a record already exists for (device, month_year) it is overwritten —
    useful when the manual rollup button is triggered more than once in
    the same month during testing.
    """
    # Upsert: delete existing record for same device+month if present
    existing = (
        db.query(MonthlySummary)
        .filter(
            MonthlySummary.device == device,
            MonthlySummary.month_year == month_year,
        )
        .first()
    )
    if existing:
        existing.last_value = last_value
        existing.usage_difference = usage_difference
        existing.created_at = datetime.utcnow()
        db.commit()
        db.refresh(existing)
        return existing

    summary = MonthlySummary(
        device=device,
        month_year=month_year,
        last_value=last_value,
        usage_difference=usage_difference,
    )
    db.add(summary)
    db.commit()
    db.refresh(summary)
    return summary


def get_all_monthly_summaries(db: Session) -> list[MonthlySummary]:
    """Return all monthly summaries ordered by device then month (newest first)."""
    return (
        db.query(MonthlySummary)
        .order_by(MonthlySummary.device, MonthlySummary.month_year.desc())
        .all()
    )


def get_previous_month_last_value(
    db: Session,
    *,
    device: str,
    year: int,
    month: int,
) -> Optional[float]:
    """
    Retrieve the `last_value` from MonthlySummary for the calendar month
    immediately *before* (year, month).  Returns None if no prior record.

    Example: year=2023, month=10  →  looks up "2023-09"
    """
    # Calculate the previous month
    if month == 1:
        prev_year, prev_month = year - 1, 12
    else:
        prev_year, prev_month = year, month - 1

    month_year_key = f"{prev_year:04d}-{prev_month:02d}"

    record = (
        db.query(MonthlySummary)
        .filter(
            MonthlySummary.device == device,
            MonthlySummary.month_year == month_year_key,
        )
        .first()
    )
    return record.last_value if record else None


def get_trend_data(db: Session, *, device: str, limit: int = 500) -> list[RawData]:
    """Return up to `limit` raw readings for `device` ordered oldest-first (for trending)."""
    return (
        db.query(RawData)
        .filter(RawData.device == device)
        .order_by(RawData.timestamp.asc())
        .limit(limit)
        .all()
    )


def get_raw_data_count(db: Session) -> int:
    """Return total number of rows in raw_data (for status display)."""
    return db.query(func.count(RawData.id)).scalar() or 0


def get_summary_count(db: Session) -> int:
    """Return total number of rows in monthly_summary."""
    return db.query(func.count(MonthlySummary.id)).scalar() or 0
