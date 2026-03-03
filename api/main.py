"""
api/main.py
-----------
FastAPI application factory.

Responsibilities:
- Create and configure the FastAPI app instance
- Register startup/shutdown lifecycle hooks
  * Startup: initialise the SQLite database & start the APScheduler
  * Shutdown: gracefully shut down the scheduler
- Mount all API routers
- Configure CORS (permissive for local dev)
- Set up structured logging
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import router as api_router
from database.models import init_db
from jobs.scheduler import start_scheduler, stop_scheduler

# ---------------------------------------------------------------------------
# Logging Configuration
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan Context Manager (replaces deprecated on_event handlers)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Code before `yield` runs on startup; code after `yield` runs on shutdown.
    Using the lifespan pattern is the modern FastAPI approach (0.93+).
    """
    # --- Startup ---
    logger.info("Reportr API starting up…")
    init_db()
    logger.info("Database initialised at reportr.db")
    start_scheduler()
    logger.info("APScheduler started — monthly rollup job registered")

    yield  # Application is now running and serving requests

    # --- Shutdown ---
    logger.info("Reportr API shutting down…")
    stop_scheduler()
    logger.info("APScheduler stopped cleanly")


# ---------------------------------------------------------------------------
# App Instance
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    app = FastAPI(
        title="Reportr API",
        description="Submeter data ingestion and reporting backend",
        version="1.0.0",
        lifespan=lifespan,
    )

    # Allow all origins for local development.
    # Restrict `allow_origins` in production environments.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router)

    return app


# Module-level instance (importable by Uvicorn and tests)
app = create_app()


# ---------------------------------------------------------------------------
# Direct-run entry point (python api/main.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )
