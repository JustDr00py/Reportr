"""
run.py
------
Reportr entry point.

Starts two components concurrently:
  1. The FastAPI/Uvicorn server  (background thread)
  2. The Textual TUI             (main thread — controls the terminal)

Architecture
------------
Uvicorn runs its own event loop in a daemon thread so that it starts and
stops independently of the Textual app.  When the TUI is quit (q / Ctrl+C),
a shutdown event signals Uvicorn to stop gracefully.

Usage
-----
    python run.py              # starts both API + TUI
    python run.py --api-only   # start only the FastAPI server (headless)
    python run.py --tui-only   # start only the TUI (API must be running separately)
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time

import uvicorn

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


# ---------------------------------------------------------------------------
# Uvicorn Server Thread
# ---------------------------------------------------------------------------

class UvicornThread(threading.Thread):
    """
    Runs the Uvicorn ASGI server in a background daemon thread.

    The `started` event is set once Uvicorn has bound to the port and is
    ready to accept connections, so the TUI can wait before launching.
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 8000):
        super().__init__(daemon=True, name="uvicorn-thread")
        self.host = host
        self.port = port
        self.started = threading.Event()
        self._server: uvicorn.Server | None = None

    def run(self) -> None:
        config = uvicorn.Config(
            app="api.main:app",
            host=self.host,
            port=self.port,
            log_level="warning",   # Keep Uvicorn quiet so TUI output is clean
            loop="asyncio",
        )
        self._server = uvicorn.Server(config)

        # Patch the lifespan startup to signal when the server is ready
        original_startup = self._server.startup

        async def startup_with_signal(sockets=None):
            await original_startup(sockets)
            self.started.set()

        self._server.startup = startup_with_signal

        import asyncio
        asyncio.run(self._server.serve())

    def stop(self) -> None:
        if self._server:
            self._server.should_exit = True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="reportr",
        description="Reportr — Submeter Reporting Application",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--api-only",
        action="store_true",
        help="Run the FastAPI server only (no TUI)",
    )
    mode.add_argument(
        "--tui-only",
        action="store_true",
        help="Run the Textual TUI only (assumes API is running separately)",
    )
    parser.add_argument("--host", default="0.0.0.0", help="API bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="API port (default: 8000)")
    return parser.parse_args()


def run_api_only(host: str, port: int) -> None:
    """Start Uvicorn in the foreground (blocking)."""
    print(f"\n  Reportr API starting on http://{host}:{port}")
    print("  Press Ctrl+C to stop.\n")
    uvicorn.run(
        "api.main:app",
        host=host,
        port=port,
        log_level="info",
    )


def run_tui_only() -> None:
    """Start the Textual TUI only (API must already be running)."""
    from ui.tui_app import ReportrApp
    app = ReportrApp()
    app.run()


def run_full(host: str, port: int) -> None:
    """
    Start the API in a background thread, wait until it's ready,
    then launch the Textual TUI in the foreground.
    """
    # 1. Start the API server thread
    server_thread = UvicornThread(host=host, port=port)
    server_thread.start()

    # 2. Wait for the server to be ready (up to 10 seconds)
    print("\n  Starting Reportr API…")
    ready = server_thread.started.wait(timeout=10)
    if not ready:
        print("  ERROR: API server did not start within 10 seconds. Aborting.")
        sys.exit(1)

    print(f"  API ready at http://{host}:{port}")
    print("  Launching TUI…\n")
    time.sleep(0.3)  # Brief pause so the startup message is visible

    # 3. Run the Textual TUI (blocks until user quits)
    try:
        from ui.tui_app import ReportrApp
        app = ReportrApp()
        app.run()
    finally:
        # 4. Shut down the API server cleanly
        print("\n  TUI exited — stopping API server…")
        server_thread.stop()
        server_thread.join(timeout=5)
        print("  Reportr stopped. Goodbye!\n")


def main() -> None:
    args = parse_args()

    if args.api_only:
        run_api_only(args.host, args.port)
    elif args.tui_only:
        run_tui_only()
    else:
        run_full(args.host, args.port)


if __name__ == "__main__":
    main()
