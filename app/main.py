"""GeoResearch Agent — FastAPI Application Entry Point.

Usage:
    uvicorn app.main:app --reload --port 8000
    # or
    python -m app.main
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.core.config import settings
from app.api.v1.health import router as health_router
from app.api.v1.auth import router as auth_router
from app.api.v1.research import router as research_router
from app.api.v1.sse import router as sse_router
from app.api.v1.interactive import router as interactive_router
from app.api.v1.wiki import router as wiki_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    logging.basicConfig(
        level=logging.DEBUG if settings.debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("uvicorn-debug.log", encoding="utf-8"),
        ],
    )
    # 抑制噪音日志
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)

    logger = logging.getLogger("app")
    logger.info(f"Starting {settings.app_name} v{settings.app_version}")

    # Create DB tables (dev convenience; use Alembic in production)
    from app.core.database import init_db
    await init_db()
    logger.info("Database tables ensured.")

    yield

    logger.info("Shutting down.")
    from app.core.database import engine
    await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="AI-powered Deep Research Agent for GIS & Remote Sensing",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS — allow all origins in development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Register routers ─────────────────────────────────────────────────

app.include_router(health_router, prefix="/api/v1", tags=["health"])
app.include_router(auth_router, prefix="/api/v1", tags=["auth"])
app.include_router(research_router, prefix="/api/v1", tags=["research"])
app.include_router(sse_router, prefix="/api/v1", tags=["streaming"])
app.include_router(interactive_router, prefix="/api/v1", tags=["interactive"])
app.include_router(wiki_router, prefix="/api/v1", tags=["wiki"])


# ── Root redirect ────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def root():
    """Serve the web UI."""
    import os
    html_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    if os.path.exists(html_path):
        return FileResponse(html_path)
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "docs": "/docs",
        "health": "/api/v1/health",
    }


# Mount Gradio UI if available (optional, use --ui flag)
try:
    from app.frontend import build_ui
    demo = build_ui()
    app = demo.mount_gradio_app(app, path="/ui")
except Exception:
    pass  # Gradio not available — use static HTML at / instead


# ── Direct run ───────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
