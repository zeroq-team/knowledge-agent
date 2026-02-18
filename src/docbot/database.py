"""Pool de conexiones asyncpg hacia Neon Postgres."""

from __future__ import annotations

import pathlib
import ssl as _ssl
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import asyncpg
import structlog

from docbot.config import Settings

logger = structlog.get_logger(__name__)

_pool: asyncpg.Pool | None = None

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "db" / "migrations"

_ASYNCPG_UNSUPPORTED_PARAMS = {"sslmode", "channel_binding"}


def _needs_ssl(dsn: str) -> bool:
    """Determina si la conexión necesita TLS."""
    return "neon.tech" in dsn or "sslmode=require" in dsn


def _clean_dsn(dsn: str) -> str:
    """Elimina parámetros de query string que asyncpg no soporta.

    asyncpg maneja SSL a través de su argumento ``ssl``, no por el
    query string, y no reconoce ``channel_binding``.
    """
    if dsn.startswith("postgresql+asyncpg://"):
        dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)

    parsed = urlparse(dsn)
    params = parse_qs(parsed.query)
    cleaned = {k: v for k, v in params.items() if k not in _ASYNCPG_UNSUPPORTED_PARAMS}
    new_query = urlencode(cleaned, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


async def create_pool(settings: Settings) -> asyncpg.Pool:
    """Crea y devuelve el pool global de conexiones."""
    global _pool

    raw_dsn = settings.database_url
    use_ssl = _needs_ssl(raw_dsn)
    dsn = _clean_dsn(raw_dsn)

    ssl: _ssl.SSLContext | bool = False
    if use_ssl:
        ssl = _ssl.create_default_context()

    _pool = await asyncpg.create_pool(
        dsn=dsn,
        min_size=2,
        max_size=10,
        ssl=ssl,
    )
    logger.info("pool_created", dsn=dsn[:50] + "…")
    return _pool


async def close_pool() -> None:
    """Cierra el pool global."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("pool_closed")


def get_pool() -> asyncpg.Pool:
    """Devuelve el pool activo o falla rápido."""
    if _pool is None:
        raise RuntimeError("El pool de DB no está inicializado. Llama a create_pool primero.")
    return _pool


async def run_migrations(pool: asyncpg.Pool) -> None:
    """Ejecuta las migraciones SQL en orden."""
    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    async with pool.acquire() as conn:
        for mig in migration_files:
            sql = mig.read_text()
            logger.info("migration_running", file=mig.name)
            await conn.execute(sql)
    logger.info("migrations_complete", count=len(migration_files))
