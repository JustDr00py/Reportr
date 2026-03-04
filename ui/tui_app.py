"""
ui/tui_app.py
-------------
Textual Terminal User Interface for Reportr.

Layout
------
  ┌─────────────────────────────────────────────────────┐
  │  Header: "⚡ Reportr"  +  status bar (counts)       │
  ├─────────────────────────────────────────────────────┤
  │  [Raw Readings]  [Monthly Summaries]  [Value Trend] │
  ├─────────────────────────────────────────────────────┤
  │  DataTable / ASCII chart (active tab)               │
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
from urllib.parse import urlencode

import httpx
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Label,
    Select,
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
# Trend Chart Widget
# ---------------------------------------------------------------------------

class TrendChart(Static):
    """
    ASCII bar chart that visualises raw meter readings over time for one device.

    Each column represents one (or an average of several) readings bucketed
    into the available terminal width.  Height is normalised to the visible
    min/max range so even small drifts become apparent.
    """

    CHART_HEIGHT = 12

    def __init__(self, *args, **kwargs):
        super().__init__("", markup=True, *args, **kwargs)
        self._device: str = ""
        self._points: list[tuple[str, float]] = []  # (timestamp_str, value)

    def update_data(self, device: str, points: list[tuple[str, float]]) -> None:
        self._device = device
        self._points = points
        self.update(self._build_markup())

    def on_resize(self) -> None:
        if self._points:
            self.update(self._build_markup())

    def _build_markup(self) -> str:
        if not self._points:
            return (
                f"[dim]No raw data found for [bold]{self._device}[/bold].  "
                f"Ingest some readings first.[/dim]"
            )

        values = [p[1] for p in self._points]
        timestamps = [p[0] for p in self._points]
        n = len(values)

        min_v = min(values)
        max_v = max(values)
        value_range = max_v - min_v or 1.0

        # 11 chars for Y-axis value + "│" = 12 chars reserved on the left
        y_label_w = 12
        chart_w = max(10, (self.size.width or 80) - y_label_w - 2)
        chart_h = self.CHART_HEIGHT

        # Bucket readings into chart_w columns
        if n <= chart_w:
            buckets = list(values)
            ts_start = timestamps[0]
            ts_end = timestamps[-1]
        else:
            step = n / chart_w
            buckets = []
            for i in range(chart_w):
                s = int(i * step)
                e = int((i + 1) * step)
                chunk = values[s:e] or [values[s]]
                buckets.append(sum(chunk) / len(chunk))
            ts_start = timestamps[0]
            ts_end = timestamps[-1]

        bw = len(buckets)

        # Quarter-value markers for the Y-axis labels
        def y_label_at(row: int) -> str:
            quarters = {
                chart_h: max_v,
                round(chart_h * 0.75): min_v + value_range * 0.75,
                round(chart_h * 0.50): min_v + value_range * 0.50,
                round(chart_h * 0.25): min_v + value_range * 0.25,
                1: min_v,
            }
            if row in quarters:
                return f"{quarters[row]:>10,.1f} \u2502"
            return " " * 11 + "\u2502"

        # Build chart rows top → bottom
        rows: list[str] = []
        for row in range(chart_h, 0, -1):
            threshold = (row - 0.5) / chart_h
            bar = "".join(
                "\u2588" if (v - min_v) / value_range >= threshold else " "
                for v in buckets
            )
            rows.append(f"[cyan]{y_label_at(row)}[/cyan][cornflower_blue]{bar}[/cornflower_blue]")

        # X-axis rule
        rows.append("[cyan]" + " " * 11 + "\u2514" + "\u2500" * bw + "[/cyan]")

        # Time range labels
        ts_s = ts_start[:16]
        ts_e = ts_end[:16]
        gap = max(0, bw - len(ts_s) - len(ts_e))
        rows.append(f"[dim]{' ' * 12}{ts_s}{' ' * gap}{ts_e}[/dim]")

        # Header stats line
        header = (
            f"[bold white]Device:[/bold white] [bold cyan]{self._device}[/bold cyan]   "
            f"[green]Min: {min_v:,.2f}[/green]   "
            f"[yellow]Max: {max_v:,.2f}[/yellow]   "
            f"[magenta]Latest: {values[-1]:,.2f}[/magenta]   "
            f"[dim]({n} readings)[/dim]"
        )

        return header + "\n\n" + "\n".join(rows)


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

    /* --- Trend tab --- */

    #trend-selector {
        height: 5;
        padding: 1 2;
        background: #16213E;
        align: left middle;
    }

    #trend-location-label {
        color: #8899cc;
        height: 3;
        content-align: left middle;
        width: 12;
    }

    #location-select {
        width: 36;
        margin-right: 2;
    }

    #trend-device-label {
        color: #8899cc;
        height: 3;
        content-align: left middle;
        width: 10;
    }

    #device-select {
        width: 36;
    }

    #trend-chart {
        height: 1fr;
        padding: 1 2;
        overflow-y: auto;
        background: #0a0a14;
        color: #e0e0ff;
    }
    """

    BINDINGS = [
        Binding("r", "refresh", "Refresh", show=True),
        Binding("t", "trigger_rollup", "Trigger Rollup", show=True),
        Binding("q", "quit", "Quit", show=True),
        Binding("1", "switch_tab('raw')", "Raw Data", show=False),
        Binding("2", "switch_tab('summary')", "Summaries", show=False),
        Binding("3", "switch_tab('trend')", "Trend", show=False),
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

            with TabPane("Value Trend", id="trend"):
                with Horizontal(id="trend-selector"):
                    yield Label(" Location: ", id="trend-location-label")
                    yield Select([], id="location-select", prompt="All locations")
                    yield Label(" Device: ", id="trend-device-label")
                    yield Select([], id="device-select", prompt="Select a device…")
                yield TrendChart(id="trend-chart")

        with Horizontal(id="action-bar"):
            yield Button("⟳  Refresh Data", id="btn-refresh", variant="primary")
            yield Button("⚙  Trigger Rollup", id="btn-rollup", variant="error")

        yield LogPanel(id="log-panel")
        yield Footer()

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    def on_mount(self) -> None:
        self._current_device: str = ""
        self._current_location: str = ""
        self._setup_tables()
        self.log_panel.push("Dashboard mounted — loading data…", "INFO")
        self.load_all_data()

    def _setup_tables(self) -> None:
        raw_table: DataTable = self.query_one("#table-raw", DataTable)
        raw_table.add_columns("ID", "Device", "Location", "Value", "Timestamp")

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
    # Data Loading — tables + device list (background worker)
    # -----------------------------------------------------------------------

    @work(exclusive=True, thread=True)
    def load_all_data(self) -> None:
        """Fetch status, raw data, summaries, and device list from the API."""
        asyncio.run(self._async_load_all())

    async def _async_load_all(self) -> None:
        try:
            devices_qs = (
                f"/devices?{urlencode({'location': self._current_location})}"
                if self._current_location else "/devices"
            )
            status_data, raw_data, summary_data, locations, devices = await asyncio.gather(
                api_get("/status"),
                api_get("/raw?limit=200"),
                api_get("/summary"),
                api_get("/locations"),
                api_get(devices_qs),
                return_exceptions=True,
            )

            if isinstance(status_data, dict):
                self.call_from_thread(self.status_bar.update_status, status_data)
            else:
                self.call_from_thread(self.log_panel.push, f"Status fetch failed: {status_data}", "WARN")

            if isinstance(raw_data, list):
                self.call_from_thread(self._populate_raw_table, raw_data)
            else:
                self.call_from_thread(self.log_panel.push, f"Raw data fetch failed: {raw_data}", "ERROR")

            if isinstance(summary_data, list):
                self.call_from_thread(self._populate_summary_table, summary_data)
            else:
                self.call_from_thread(self.log_panel.push, f"Summary fetch failed: {summary_data}", "ERROR")

            if isinstance(locations, list):
                self.call_from_thread(self._populate_location_select, locations)
            else:
                self.call_from_thread(self.log_panel.push, f"Location list fetch failed: {locations}", "WARN")

            if isinstance(devices, list):
                self.call_from_thread(self._populate_device_select, devices)
            else:
                self.call_from_thread(self.log_panel.push, f"Device list fetch failed: {devices}", "WARN")

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
            if "." in ts:
                ts = ts[:19]
            table.add_row(
                str(row.get("id", "")),
                row.get("device", ""),
                row.get("location") or "",
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

    def _populate_location_select(self, locations: list[str]) -> None:
        select: Select = self.query_one("#location-select", Select)
        select.set_options([(loc, loc) for loc in locations])
        # Restore current selection if still valid; otherwise leave as "All locations"
        if self._current_location in locations:
            select.value = self._current_location

    def _populate_device_select(self, devices: list[str]) -> None:
        select: Select = self.query_one("#device-select", Select)
        options = [(d, d) for d in devices]
        select.set_options(options)

        if devices:
            target = self._current_device if self._current_device in devices else devices[0]
            select.value = target
            # Explicitly load trend in case the value didn't change (no Changed event)
            self.load_trend_data(target)

    # -----------------------------------------------------------------------
    # Device list loading (filtered by location)
    # -----------------------------------------------------------------------

    @work(exclusive=True, thread=True)
    def load_devices_for_location(self, location: str) -> None:
        asyncio.run(self._async_load_devices(location))

    async def _async_load_devices(self, location: str) -> None:
        try:
            qs = f"?{urlencode({'location': location})}" if location else ""
            devices = await api_get(f"/devices{qs}")
            if isinstance(devices, list):
                self.call_from_thread(self._populate_device_select, devices)
        except Exception as exc:
            self.call_from_thread(self.log_panel.push, f"Device list error: {exc}", "WARN")

    # -----------------------------------------------------------------------
    # Trend Data Loading
    # -----------------------------------------------------------------------

    @work(exclusive=True, thread=True)
    def load_trend_data(self, device: str) -> None:
        """Fetch trend data for `device` and update the chart."""
        asyncio.run(self._async_load_trend(device))

    async def _async_load_trend(self, device: str) -> None:
        try:
            qs = urlencode({"device": device, "limit": 500})
            data = await api_get(f"/trend?{qs}")
            if isinstance(data, list):
                points = [(r["timestamp"], r["value"]) for r in data]
                self.call_from_thread(self._update_trend_chart, device, points)
            else:
                self.call_from_thread(self.log_panel.push, f"Trend fetch failed: {data}", "ERROR")
        except (httpx.ConnectError, httpx.ConnectTimeout):
            self.call_from_thread(
                self.log_panel.push, "Cannot reach API — is the server running?", "ERROR"
            )
        except Exception as exc:
            self.call_from_thread(self.log_panel.push, f"Trend error: {exc}", "ERROR")

    def _update_trend_chart(self, device: str, points: list[tuple[str, float]]) -> None:
        self._current_device = device
        chart: TrendChart = self.query_one("#trend-chart", TrendChart)
        chart.update_data(device, points)

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
            short = message[:120].replace("\n", " | ")
            self.call_from_thread(self.log_panel.push, f"Rollup: {short}", "OK")
            await self._async_load_all()
        except httpx.HTTPStatusError as exc:
            self.call_from_thread(
                self.log_panel.push,
                f"Rollup failed (HTTP {exc.response.status_code}): {exc.response.text[:80]}",
                "ERROR",
            )
        except (httpx.ConnectError, httpx.ConnectTimeout):
            self.call_from_thread(
                self.log_panel.push, "Cannot reach API — is the server running?", "ERROR"
            )
        except Exception as exc:
            self.call_from_thread(self.log_panel.push, f"Rollup error: {exc}", "ERROR")

    # -----------------------------------------------------------------------
    # Button / Select Handlers
    # -----------------------------------------------------------------------

    @on(Button.Pressed, "#btn-refresh")
    def on_refresh_pressed(self) -> None:
        self.log_panel.push("Refreshing data…", "INFO")
        self.load_all_data()
        if self._current_device:
            self.load_trend_data(self._current_device)

    @on(Button.Pressed, "#btn-rollup")
    def on_rollup_pressed(self) -> None:
        self.trigger_rollup()

    @on(Select.Changed, "#location-select")
    def on_location_select_changed(self, event: Select.Changed) -> None:
        self._current_location = "" if event.value is Select.BLANK else str(event.value)
        self.load_devices_for_location(self._current_location)

    @on(Select.Changed, "#device-select")
    def on_device_select_changed(self, event: Select.Changed) -> None:
        if event.value is not Select.BLANK:
            self.load_trend_data(str(event.value))

    # -----------------------------------------------------------------------
    # Keybinding Actions
    # -----------------------------------------------------------------------

    def action_refresh(self) -> None:
        self.log_panel.push("Refreshing data…", "INFO")
        self.load_all_data()
        if self._current_device:
            self.load_trend_data(self._current_device)

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
