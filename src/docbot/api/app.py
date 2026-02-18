"""FastAPI app factory con lifespan para pool Neon y migraciones."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, Response

from docbot import __version__
from docbot.config import get_settings
from docbot.database import close_pool, create_pool, run_migrations

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicializa pool de Neon + migraciones al arrancar; cierra al parar."""
    settings = get_settings()
    pool = await create_pool(settings)
    await run_migrations(pool)
    app.state.pool = pool
    app.state.settings = settings
    logger.info("app_started", version=__version__)
    yield
    await close_pool()
    logger.info("app_stopped")


def create_app() -> FastAPI:
    """Factory que crea la app FastAPI con todos los routers."""
    app = FastAPI(
        title="ZeroQ Docbot",
        description="RAG + Knowledge Graph agent para documentación interna",
        version=__version__,
        lifespan=lifespan,
    )

    # --- Middleware: request_id + timing ---
    @app.middleware("http")
    async def request_logging(request: Request, call_next):
        request_id = str(uuid.uuid4())[:8]
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        import time

        t0 = time.time()
        response: Response = await call_next(request)
        duration_ms = round((time.time() - t0) * 1000, 1)

        logger.info(
            "request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=duration_ms,
        )
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Duration-Ms"] = str(duration_ms)
        return response

    # --- Routers ---
    from docbot.api.routes.answer import router as answer_router
    from docbot.api.routes.chat import router as chat_router
    from docbot.api.routes.health import router as health_router
    from docbot.api.routes.search import router as search_router
    from docbot.api.routes.sync import router as sync_router

    app.include_router(health_router, tags=["health"])
    app.include_router(search_router, tags=["search"])
    app.include_router(answer_router, tags=["rag"])
    app.include_router(chat_router, tags=["chat"])
    app.include_router(sync_router, tags=["indexer"])

    # --- Chat UI (archivos estáticos) ---
    import pathlib

    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    static_dir = pathlib.Path(__file__).resolve().parent.parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/", include_in_schema=False)
    async def root():
        return FileResponse(str(static_dir / "index.html"))

    return app
