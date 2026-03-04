"""
database/models.py
------------------
SQLAlchemy ORM models and async engine/session setup.

Uses a single SQLite database file (reportr.db) at the project root.
The async engine (aiosqlite) allows non-blocking DB access from both
the FastAPI request handlers and the APScheduler job.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# Resolve DB path relative to this file so it always lands at project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "reportr.db"

# SQLite connection URL – synchronous driver (sqlite3 stdlib)
DATABASE_URL = f"sqlite:///{DB_PATH}"


# ---------------------------------------------------------------------------
# Engine & Session Factory
# ---------------------------------------------------------------------------

engine = create_engine(
    DATABASE_URL,
    # Required for SQLite: allow the same connection to be used across threads.
    # APScheduler and FastAPI/Uvicorn run in different threads.
    connect_args={"check_same_thread": False},
    echo=False,  # Set True to log every SQL statement for debugging
)


# Enable WAL mode for better concurrent read/write performance with SQLite
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


# Session factory — use as a context manager in all DB operations
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,  # Keep objects usable after session closes
)


# ---------------------------------------------------------------------------
# Declarative Base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Table 1: RawData
# ---------------------------------------------------------------------------

class RawData(Base):
    """
    Stores every individual submeter ping received via POST /api/ingest.

    Records are pruned after the monthly rollup job processes them,
    keeping the database lean. Only the aggregated MonthlySummary is kept
    long-term.
    """

    __tablename__ = "raw_data"

    id: int = Column(Integer, primary_key=True, index=True, autoincrement=True)
    device: str = Column(String(128), nullable=False, index=True)
    location: str = Column(String(256), nullable=True)
    value: float = Column(Float, nullable=False)
    # Store as UTC datetime; the ingest endpoint normalises timezone info
    timestamp: datetime = Column(DateTime, nullable=False, index=True)

    def __repr__(self) -> str:
        return (
            f"<RawData id={self.id} device={self.device!r} "
            f"location={self.location!r} value={self.value} ts={self.timestamp}>"
        )


# ---------------------------------------------------------------------------
# Table 2: MonthlySummary
# ---------------------------------------------------------------------------

class MonthlySummary(Base):
    """
    Stores one rolled-up record per device per calendar month.

    `last_value`      — the meter reading at the end of the reported month
    `usage_difference`— consumption during that month
                        (last_value − previous month's last_value)

    A composite unique constraint prevents duplicate summaries.
    """

    __tablename__ = "monthly_summary"

    id: int = Column(Integer, primary_key=True, index=True, autoincrement=True)
    device: str = Column(String(128), nullable=False, index=True)
    # Human-readable key, e.g. "2023-10"
    month_year: str = Column(String(7), nullable=False, index=True)
    last_value: float = Column(Float, nullable=False)
    usage_difference: float = Column(Float, nullable=False)
    # When this summary record was created
    created_at: datetime = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("device", "month_year", name="uq_device_month"),
    )

    def __repr__(self) -> str:
        return (
            f"<MonthlySummary id={self.id} device={self.device!r} "
            f"month={self.month_year} usage={self.usage_difference}>"
        )


# ---------------------------------------------------------------------------
# Database Initialisation Helper
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Create all tables if they do not already exist."""
    Base.metadata.create_all(bind=engine)
