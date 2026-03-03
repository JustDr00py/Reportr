"""
ui/tui_app.py
-------------
Textual Terminal User Interface for Reportr.

Layout
------
  ┌─────────────────────────────────────────────────────┐
  │  Header: "⚡ Reportr"  +  status bar (counts)       │
  ├─────────────────────────────────────────────────────┤
  │  [Raw Readings]  [Monthly Summaries]   ← Tab bar    │
  ├─────────────────────────────────────────────────────┤
  │  DataTable (active tab)                             │
  ├─────────────────────────────────────────────────────┤
  │  [Refresh Data]  [Trigger Rollup]   ← Action bar   │
  ├─────────────────────────────────────────────────────┤
  │  Footer: log messages                               │
  └─────────────────────────────────────────────────────┘

The TUI calls the local FastAPI server (http://127.0.0.1:8000) via httpx
rather than hitting the database directly, keeping concerns separated.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

import httpx
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Label,
    LoadingIndicator,
    Static,
    TabbedContent,
    TabPane,
)

logger = logging.getLogger(__name__)

API_BASE = "http://127.0.0.1:8000/api"
REQUEST_TIMEOUT = 10.0  # seconds


# ---------------------------------------------------------------------------
# Helper: safe API calls
# ---------------------------------------------------------------------------

async def api_get(path: str) -> Any:
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.get(f"{API_BASE}{path}")
        resp.raise_for_status()
        return resp.json()


async def api_post(path: str, json: dict | None = None) -> Any:
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.post(f"{API_BASE}{path}", json=json or {})
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# Status Bar Widget
# ---------------------------------------------------------------------------

class StatusBar(Static):
    """Displays live counts and the next scheduled rollup time."""

    raw_count: reactive[int] = reactive(0)
    summary_count: reactive[int] = reactive(0)
    api_status: reactive[str] = reactive("connecting…")

    def render(self) -> str:
        return (
            f"  API: {self.api_status}   |   "
            f"Raw Records: {self.raw_count}   |   "
            f"Monthly Summaries: {self.summary_count}"
        )

    def update_status(self, data: dict) -> None:
        self.raw_count = data.get("raw_data_count", 0)
        self.summary_count = data.get("summary_count", 0)
        self.api_status = data.get("status", "unknown")


# ---------------------------------------------------------------------------
# Log Panel Widget
# ---------------------------------------------------------------------------

class LogPanel(Static):
    """Simple scrolling log area at the bottom of the screen."""

    MAX_LINES = 6

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._lines: list[str] = []

    def push(self, msg: str, level: str = "INFO") -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        colour_map = {"INFO": "green", "WARN": "yellow", "ERROR": "red", "OK": "cyan"}
        colour = colour_map.get(level, "white")
        self._lines.append(f"[{colour}][{ts}][/{colour}] {msg}")
        if len(self._lines) > self.MAX_LINES:
            self._lines = self._lines[-self.MAX_LINES:]
        self.update("\n".join(self._lines))


# ---------------------------------------------------------------------------
# Main Application
# ---------------------------------------------------------------------------

class ReportrApp(App):
    """Reportr Terminal Dashboard."""

    CSS = """
    Screen {
        background: #0f0f1a;
    }

    Header {
        background: #1A1A2E;
        color: #e0e0ff;
        text-style: bold;
    }

    Footer {
        background: #1A1A2E;
        color: #888899;
    }

    #status-bar {
        background: #16213E;
        color: #8899cc;
        height: 1;
        padding: 0 2;
    }

    TabbedContent {
        height: 1fr;
    }

    TabPane {
        padding: 0 1;
    }

    DataTable {
        height: 1fr;
        border: solid #0F3460;
    }

    DataTable > .datatable--header {
        background: #0F3460;
        color: #ffffff;
        text-style: bold;
    }

    DataTable > .datatable--cursor {
        background: #E94560 20%;
        color: #ffffff;
    }

    #action-bar {
        height: 3;
        background: #16213E;
        align: center middle;
        padding: 0 2;
    }

    Button {
        margin: 0 1;
    }

    #btn-refresh {
        background: #0F3460;
        color: #ffffff;
        border: solid #1a5c99;
    }

    #btn-refresh:hover {
        background: #1a5c99;
    }

    #btn-rollup {
        background: #8B1A2E;
        color: #ffffff;
        border: solid #E94560;
    }

    #btn-rollup:hover {
        background: #E94560;
    }

    #log-panel {
        background: #0a0a14;
        color: #778899;
        height: 8;
        border-top: solid #1A1A2E;
        padding: 0 2;
        overflow-y: auto;
    }

    LoadingIndicator {
        background: #0f0f1a;
    }
    """

    BINDINGS = [
        Binding("r", "refresh", "Refresh", show=True),
        Binding("t", "trigger_rollup", "Trigger Rollup", show=True),
        Binding("q", "quit", "Quit", show=True),
        Binding("1", "switch_tab('raw')", "Raw Data", show=False),
        Binding("2", "switch_tab('summary')", "Summaries", show=False),
    ]

    TITLE = "⚡ Reportr — Submeter Dashboard"

    def compose(self) -> ComposeResult:
        yield Header()
        yield StatusBar(id="status-bar")

        with TabbedContent(initial="raw"):
            with TabPane("Raw Readings", id="raw"):
                yield DataTable(id="table-raw", zebra_stripes=True, cursor_type="row")

            with TabPane("Monthly Summaries", id="summary"):
                yield DataTable(id="table-summary", zebra_stripes=True, cursor_type="row")

        with Horizontal(id="action-bar"):
            yield Button("⟳  Refresh Data", id="btn-refresh", variant="primary")
            yield Button("⚙  Trigger Rollup", id="btn-rollup", variant="error")

        yield LogPanel(id="log-panel")
        yield Footer()

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    def on_mount(self) -> None:
        self._setup_tables()
        self.log_panel.push("Dashboard mounted — loading data…", "INFO")
        self.load_all_data()

    def _setup_tables(self) -> None:
        raw_table: DataTable = self.query_one("#table-raw", DataTable)
        raw_table.add_columns("ID", "Device", "Value", "Timestamp")

        summary_table: DataTable = self.query_one("#table-summary", DataTable)
        summary_table.add_columns(
            "ID", "Device", "Month/Year", "Last Value", "Usage", "Created At"
        )

    @property
    def log_panel(self) -> LogPanel:
        return self.query_one("#log-panel", LogPanel)

    @property
    def status_bar(self) -> StatusBar:
        return self.query_one("#status-bar", StatusBar)

    # -----------------------------------------------------------------------
    # Data Loading (runs in background worker thread)
    # -----------------------------------------------------------------------

    @work(exclusive=True, thread=True)
    def load_all_data(self) -> None:
        """Fetch all data from the API and update the tables."""
        asyncio.run(self._async_load_all())

    async def _async_load_all(self) -> None:
        try:
            status_data, raw_data, summary_data = await asyncio.gather(
                api_get("/status"),
                api_get("/raw?limit=200"),
                api_get("/summary"),
                return_exceptions=True,
            )

            # Update status bar
            if isinstance(status_data, dict):
                self.call_from_thread(self.status_bar.update_status, status_data)
            else:
                self.call_from_thread(self.log_panel.push, f"Status fetch failed: {status_data}", "WARN")

            # Update raw table
            if isinstance(raw_data, list):
                self.call_from_thread(self._populate_raw_table, raw_data)
            else:
                self.call_from_thread(self.log_panel.push, f"Raw data fetch failed: {raw_data}", "ERROR")

            # Update summary table
            if isinstance(summary_data, list):
                self.call_from_thread(self._populate_summary_table, summary_data)
            else:
                self.call_from_thread(self.log_panel.push, f"Summary fetch failed: {summary_data}", "ERROR")

            self.call_from_thread(
                self.log_panel.push,
                f"Data refreshed — {len(raw_data) if isinstance(raw_data, list) else '?'} raw, "
                f"{len(summary_data) if isinstance(summary_data, list) else '?'} summaries",
                "OK",
            )

        except (httpx.ConnectError, httpx.ConnectTimeout):
            self.call_from_thread(
                self.log_panel.push,
                "Cannot reach API at http://127.0.0.1:8000 — is the server running?",
                "ERROR",
            )
        except Exception as exc:
            self.call_from_thread(self.log_panel.push, f"Unexpected error: {exc}", "ERROR")

    def _populate_raw_table(self, rows: list[dict]) -> None:
        table: DataTable = self.query_one("#table-raw", DataTable)
        table.clear()
        for row in rows:
            ts = row.get("timestamp", "")
            # Trim microseconds for display
            if "." in ts:
                ts = ts[:19]
            table.add_row(
                str(row.get("id", "")),
                row.get("device", ""),
                f"{row.get('value', 0):,.2f}",
                ts,
            )

    def _populate_summary_table(self, rows: list[dict]) -> None:
        table: DataTable = self.query_one("#table-summary", DataTable)
        table.clear()
        for row in rows:
            created = row.get("created_at", "")
            if "." in created:
                created = created[:19]
            table.add_row(
                str(row.get("id", "")),
                row.get("device", ""),
                row.get("month_year", ""),
                f"{row.get('last_value', 0):,.2f}",
                f"{row.get('usage_difference', 0):,.2f}",
                created,
            )

    # -----------------------------------------------------------------------
    # Rollup (background worker)
    # -----------------------------------------------------------------------

    @work(exclusive=True, thread=True)
    def trigger_rollup(self) -> None:
        self.call_from_thread(self.log_panel.push, "Triggering monthly rollup…", "INFO")
        asyncio.run(self._async_rollup())

    async def _async_rollup(self) -> None:
        try:
            result = await api_post("/rollup")
            message = result.get("message", str(result))
            # Show first 120 chars of message in log
            short = message[:120].replace("\n", " | ")
            self.call_from_thread(self.log_panel.push, f"Rollup: {short}", "OK")
            # Refresh data after rollup
            await self._async_load_all()
        except httpx.HTTPStatusError as exc:
            self.call_from_thread(
                self.log_panel.push,
                f"Rollup failed (HTTP {exc.response.status_code}): {exc.response.text[:80]}",
                "ERROR",
            )
        except (httpx.ConnectError, httpx.ConnectTimeout):
            self.call_from_thread(
                self.log_panel.push,
                "Cannot reach API — is the server running?",
                "ERROR",
            )
        except Exception as exc:
            self.call_from_thread(self.log_panel.push, f"Rollup error: {exc}", "ERROR")

    # -----------------------------------------------------------------------
    # Button Handlers
    # -----------------------------------------------------------------------

    @on(Button.Pressed, "#btn-refresh")
    def on_refresh_pressed(self) -> None:
        self.log_panel.push("Refreshing data…", "INFO")
        self.load_all_data()

    @on(Button.Pressed, "#btn-rollup")
    def on_rollup_pressed(self) -> None:
        self.trigger_rollup()

    # -----------------------------------------------------------------------
    # Keybinding Actions
    # -----------------------------------------------------------------------

    def action_refresh(self) -> None:
        self.log_panel.push("Refreshing data…", "INFO")
        self.load_all_data()

    def action_trigger_rollup(self) -> None:
        self.trigger_rollup()

    def action_switch_tab(self, tab_id: str) -> None:
        tabbed: TabbedContent = self.query_one(TabbedContent)
        tabbed.active = tab_id


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = ReportrApp()
    app.run()
