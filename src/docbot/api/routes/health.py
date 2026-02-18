"""Endpoint de health check."""

from __future__ import annotations

from fastapi import APIRouter, Request

from docbot import __version__
from docbot.api.schemas import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    """Retorna el estado del servicio y la conexión a Neon."""
    pool = request.app.state.pool
    db_ok = False

    try:
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        db_ok = True
    except Exception:
        pass

    return HealthResponse(
        status="ok" if db_ok else "degraded",
        db_connected=db_ok,
        version=__version__,
    )
