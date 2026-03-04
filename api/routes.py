"""
api/routes.py
-------------
FastAPI route definitions for the Reportr API.

Endpoints
---------
POST /api/ingest   — Accept a submeter reading and persist it to raw_data
GET  /api/status   — Return basic counts (useful for health checks / TUI)
GET  /api/raw      — Return recent raw entries (JSON, for TUI polling)
GET  /api/summary  — Return all monthly summaries (JSON, for TUI polling)
POST /api/rollup   — Manually trigger the monthly rollup (testing / TUI button)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from database.crud import (
    create_raw_entry,
    get_all_monthly_summaries,
    get_distinct_devices,
    get_distinct_locations,
    get_recent_raw_data,
    get_raw_data_count,
    get_summary_count,
    get_trend_data,
)
from database.models import SessionLocal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["Reportr API"])


# ---------------------------------------------------------------------------
# Dependency: DB Session
# ---------------------------------------------------------------------------

def get_db():
    """Yield a SQLAlchemy session and ensure it's closed after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------

class IngestPayload(BaseModel):
    """
    Incoming submeter reading.

    The `Timestamp` field accepts any ISO-8601 string.  If timezone info is
    present it is normalised to UTC; naive datetimes are assumed to be UTC.
    """

    Device: str = Field(..., min_length=1, max_length=128, examples=["Meter_A"])
    Location: str = Field("", max_length=256, examples=["Building A - Floor 2"])
    Value: float = Field(..., description="Current meter reading (cumulative)")
    Timestamp: datetime = Field(..., examples=["2023-10-25T14:30:00Z"])

    @field_validator("Timestamp", mode="before")
    @classmethod
    def parse_timestamp(cls, v: Any) -> datetime:
        """Accept strings and datetime objects; normalise to UTC-naive."""
        if isinstance(v, str):
            v = datetime.fromisoformat(v.replace("Z", "+00:00"))
        if isinstance(v, datetime):
            if v.tzinfo is not None:
                # Convert to UTC then strip tzinfo for consistent DB storage
                v = v.astimezone(timezone.utc).replace(tzinfo=None)
        return v

    @field_validator("Device")
    @classmethod
    def strip_device(cls, v: str) -> str:
        return v.strip()


class IngestResponse(BaseModel):
    success: bool
    id: int
    device: str
    location: str
    value: float
    timestamp: datetime


class StatusResponse(BaseModel):
    status: str
    raw_data_count: int
    summary_count: int


class RawDataItem(BaseModel):
    id: int
    device: str
    location: str | None = None
    value: float
    timestamp: datetime

    model_config = {"from_attributes": True}


class SummaryItem(BaseModel):
    id: int
    device: str
    month_year: str
    last_value: float
    usage_difference: float
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post(
    "/ingest",
    response_model=IngestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest a submeter reading",
)
def ingest(payload: IngestPayload, db: Session = Depends(get_db)) -> IngestResponse:
    """
    Accept a single submeter reading and persist it to the raw_data table.

    Returns the created record including the auto-assigned `id`.
    """
    try:
        entry = create_raw_entry(
            db,
            device=payload.Device,
            location=payload.Location,
            value=payload.Value,
            timestamp=payload.Timestamp,
        )
        logger.info(
            "Ingested: device=%s location=%s value=%s ts=%s",
            entry.device, entry.location, entry.value, entry.timestamp,
        )
        return IngestResponse(
            success=True,
            id=entry.id,
            device=entry.device,
            location=entry.location or "",
            value=entry.value,
            timestamp=entry.timestamp,
        )
    except Exception as exc:
        logger.exception("Ingest failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to store reading: {exc}",
        )


@router.get("/status", response_model=StatusResponse, summary="API health/status")
def api_status(db: Session = Depends(get_db)) -> StatusResponse:
    return StatusResponse(
        status="ok",
        raw_data_count=get_raw_data_count(db),
        summary_count=get_summary_count(db),
    )


@router.get("/raw", response_model=list[RawDataItem], summary="Recent raw readings")
def list_raw(limit: int = 100, db: Session = Depends(get_db)) -> list[RawDataItem]:
    """Return the most recent raw data entries (default 100)."""
    rows = get_recent_raw_data(db, limit=max(1, min(limit, 1000)))
    return [RawDataItem.model_validate(r) for r in rows]


@router.get(
    "/summary",
    response_model=list[SummaryItem],
    summary="All monthly summaries",
)
def list_summaries(db: Session = Depends(get_db)) -> list[SummaryItem]:
    """Return all monthly summary records."""
    rows = get_all_monthly_summaries(db)
    return [SummaryItem.model_validate(r) for r in rows]


@router.get("/locations", response_model=list[str], summary="List all location names")
def list_locations(db: Session = Depends(get_db)) -> list[str]:
    """Return sorted list of all unique non-empty location values in raw_data."""
    return get_distinct_locations(db)


@router.get("/devices", response_model=list[str], summary="List all device names")
def list_devices(location: str | None = None, db: Session = Depends(get_db)) -> list[str]:
    """Return sorted list of unique device names, optionally filtered by location."""
    return get_distinct_devices(db, location=location or None)


@router.get("/trend", response_model=list[RawDataItem], summary="Trend data for a device")
def get_device_trend(device: str, limit: int = 500, db: Session = Depends(get_db)) -> list[RawDataItem]:
    """Return raw readings for a specific device ordered oldest-first (for charting)."""
    rows = get_trend_data(db, device=device, limit=max(1, min(limit, 2000)))
    return [RawDataItem.model_validate(r) for r in rows]


@router.post("/rollup", summary="Manually trigger monthly rollup")
def manual_rollup() -> dict:
    """
    Trigger the monthly rollup job immediately (for testing / TUI button).

    This runs the same logic as the scheduled job but uses 'last month'
    relative to the current date.
    """
    # Import here to avoid circular imports at module load time
    from jobs.scheduler import run_monthly_rollup

    try:
        result = run_monthly_rollup()
        return {"success": True, "message": result}
    except Exception as exc:
        logger.exception("Manual rollup failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Rollup failed: {exc}",
        )
