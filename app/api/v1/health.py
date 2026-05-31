"""Health check endpoint."""
from __future__ import annotations

from fastapi import APIRouter

from app.core.config import settings
from app.schemas.research import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """Basic health check for load balancers and monitoring."""
    return HealthResponse(
        status="ok",
        version=settings.app_version,
        components={
            "api": "ok",
        },
    )
