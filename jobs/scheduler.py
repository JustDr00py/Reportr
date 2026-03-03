"""
jobs/scheduler.py
-----------------
APScheduler setup and the monthly rollup job logic.

Rollup Logic (runs at 00:01 on the 1st of every month)
-------------------------------------------------------
For every unique device in raw_data:

  1.  Find the raw record with the LATEST timestamp in the *previous* month.
      This is the closing meter value for that month.

  2.  Look up the MonthlySummary for the month BEFORE the previous month
      to get the opening value (last_value).

  3.  Calculate usage = closing_value − opening_value.
      (If no prior summary exists, usage equals closing_value — first month.)

  4.  Write a new MonthlySummary record for the previous month.

  5.  Generate a PDF report for the device/month.

  6.  Delete the raw_data rows for that device/month (space pruning).
"""

from __future__ import annotations

import logging
from calendar import monthrange
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from database.crud import (
    create_monthly_summary,
    delete_raw_data_for_month,
    get_distinct_devices,
    get_last_raw_entry_for_month,
    get_previous_month_last_value,
)
from database.models import SessionLocal

logger = logging.getLogger(__name__)

# Module-level scheduler instance (singleton)
_scheduler = BackgroundScheduler(timezone="UTC")


# ---------------------------------------------------------------------------
# Core Rollup Logic
# ---------------------------------------------------------------------------

def run_monthly_rollup(target_date: datetime | None = None) -> str:
    """
    Execute the monthly rollup for the month immediately prior to `target_date`.

    Parameters
    ----------
    target_date : datetime, optional
        The reference point.  Defaults to *now* (UTC).
        Example: if today is 2023-11-01, it will roll up October 2023.

    Returns
    -------
    str
        A human-readable summary message describing what was processed.
    """
    if target_date is None:
        target_date = datetime.utcnow()

    # Determine the previous month
    first_of_this_month = target_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_of_prev_month = first_of_this_month - timedelta(seconds=1)
    prev_year = last_of_prev_month.year
    prev_month = last_of_prev_month.month
    month_year_key = f"{prev_year:04d}-{prev_month:02d}"

    logger.info("=== Monthly Rollup started for %s ===", month_year_key)

    db = SessionLocal()
    results: list[str] = []

    try:
        devices = get_distinct_devices(db)

        if not devices:
            msg = f"Rollup for {month_year_key}: no devices found in raw_data — nothing to do."
            logger.warning(msg)
            return msg

        for device in devices:
            try:
                _process_device(db, device, prev_year, prev_month, month_year_key)
                results.append(f"  ✓  {device}")
            except Exception as exc:
                logger.exception("Rollup failed for device %s: %s", device, exc)
                results.append(f"  ✗  {device} — ERROR: {exc}")

    finally:
        db.close()

    summary = f"Rollup for {month_year_key} complete.\n" + "\n".join(results)
    logger.info(summary)
    return summary


def _process_device(db, device: str, year: int, month: int, month_year_key: str) -> None:
    """
    Run the full rollup pipeline for a single device in a given month.

    All DB interactions for this device happen within this function so that
    a failure for one device doesn't abort the others.
    """
    # Step 1 — Find the last raw record for the previous month
    last_entry = get_last_raw_entry_for_month(db, device=device, year=year, month=month)

    if last_entry is None:
        logger.info(
            "Device %s: no raw data found for %s — skipping.",
            device, month_year_key
        )
        return

    closing_value = last_entry.value
    logger.info("Device %s: closing value for %s = %s", device, month_year_key, closing_value)

    # Step 2 — Get the opening value from the MonthlySummary of the prior month
    opening_value = get_previous_month_last_value(db, device=device, year=year, month=month)

    if opening_value is None:
        # First month on record — usage equals the closing value itself
        logger.info(
            "Device %s: no prior summary found; treating closing value as usage.",
            device
        )
        usage = closing_value
        opening_value = 0.0
    else:
        usage = closing_value - opening_value
        logger.info(
            "Device %s: opening=%s closing=%s usage=%s",
            device, opening_value, closing_value, usage
        )

    # Step 3 — Persist the MonthlySummary record
    summary = create_monthly_summary(
        db,
        device=device,
        month_year=month_year_key,
        last_value=closing_value,
        usage_difference=usage,
    )
    logger.info("Device %s: MonthlySummary id=%s created/updated.", device, summary.id)

    # Step 4 — Generate the PDF report
    try:
        from reporting.pdf_generator import generate_report
        report_path = generate_report(
            device=device,
            month_year=month_year_key,
            opening_value=opening_value,
            closing_value=closing_value,
            usage=usage,
        )
        logger.info("Device %s: PDF report saved to %s", device, report_path)
    except Exception as exc:
        # PDF failure should not abort the DB operations
        logger.exception("Device %s: PDF generation failed: %s", device, exc)

    # Step 5 — Prune raw_data for this device/month
    deleted_count = delete_raw_data_for_month(db, device=device, year=year, month=month)
    logger.info("Device %s: pruned %d raw_data rows for %s.", device, deleted_count, month_year_key)


# ---------------------------------------------------------------------------
# Scheduler Lifecycle
# ---------------------------------------------------------------------------

def start_scheduler() -> None:
    """
    Start the background scheduler and register the monthly cron job.
    Safe to call multiple times — checks `running` before starting.
    """
    if not _scheduler.running:
        # Schedule: 00:01 on the 1st of every month (UTC)
        _scheduler.add_job(
            run_monthly_rollup,
            trigger=CronTrigger(day=1, hour=0, minute=1, timezone="UTC"),
            id="monthly_rollup",
            name="Monthly Submeter Rollup",
            replace_existing=True,
            misfire_grace_time=3600,  # Allow up to 1 h late if server was down
        )
        _scheduler.start()
        logger.info(
            "Scheduler started. Next rollup: %s",
            _scheduler.get_job("monthly_rollup").next_run_time,
        )


def stop_scheduler() -> None:
    """Gracefully shut down the scheduler (waits for running jobs to finish)."""
    if _scheduler.running:
        _scheduler.shutdown(wait=True)
        logger.info("Scheduler stopped.")


def get_next_run_time() -> str:
    """Return the next scheduled rollup time as a formatted string."""
    job = _scheduler.get_job("monthly_rollup")
    if job and job.next_run_time:
        return job.next_run_time.strftime("%Y-%m-%d %H:%M:%S UTC")
    return "Scheduler not running"
