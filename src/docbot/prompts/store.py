"""Store de prompts versionados: lectura del prompt activo con cache en proceso
y hot-reload, más operaciones de versionado (crear versión, activar/rollback).

El agente y el pipeline RAG leen el prompt activo por request vía `get_active`.
Al publicar una versión nueva (`create_version`) o hacer rollback (`set_active`)
se invalida la cache, así el próximo request usa el contenido nuevo sin reiniciar
el proceso.
"""

from __future__ import annotations

import time

import structlog

from docbot.database import get_pool

logger = structlog.get_logger(__name__)

# TTL corto para que, aun sin invalidación explícita (ej: otra instancia publicó),
# el prompt se refresque solo. La invalidación explícita cubre la instancia local.
_CACHE_TTL_SECONDS = 30.0

# key -> (content, version, fetched_at_monotonic)
_cache: dict[str, tuple[str, int, float]] = {}


def invalidate(key: str | None = None) -> None:
    """Invalida la cache de un prompt (o de todos si key es None)."""
    if key is None:
        _cache.clear()
    else:
        _cache.pop(key, None)


async def _fetch_active(key: str) -> tuple[str, int] | None:
    pool = get_pool()
    row = await pool.fetchrow(
        """
        SELECT pv.content, pv.version
        FROM prompts p
        JOIN prompt_versions pv ON pv.key = p.key AND pv.version = p.active_version
        WHERE p.key = $1
        """,
        key,
    )
    if row is None:
        return None
    return row["content"], row["version"]


async def get_active_with_version(key: str) -> tuple[str, int] | None:
    """Devuelve (content, version) del prompt activo, con cache TTL en proceso."""
    cached = _cache.get(key)
    if cached is not None:
        content, version, fetched_at = cached
        if (time.monotonic() - fetched_at) < _CACHE_TTL_SECONDS:
            return content, version

    result = await _fetch_active(key)
    if result is None:
        return None
    content, version = result
    _cache[key] = (content, version, time.monotonic())
    return content, version


async def get_active(key: str) -> str | None:
    """Devuelve el contenido del prompt activo, o None si no existe."""
    result = await get_active_with_version(key)
    return result[0] if result else None


async def list_prompts() -> list[dict]:
    """Lista los prompts con su versión activa y metadatos (para el panel admin)."""
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT
            p.key,
            p.description,
            p.active_version,
            p.updated_at,
            (SELECT count(*) FROM prompt_versions v WHERE v.key = p.key) AS versions_count
        FROM prompts p
        ORDER BY p.key
        """
    )
    return [dict(r) for r in rows]


async def list_versions(key: str) -> list[dict]:
    """Lista todas las versiones de un prompt, más recientes primero."""
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT pv.id, pv.version, pv.content, pv.note, pv.author, pv.created_at,
               (pv.version = p.active_version) AS is_active
        FROM prompt_versions pv
        JOIN prompts p ON p.key = pv.key
        WHERE pv.key = $1
        ORDER BY pv.version DESC
        """,
        key,
    )
    return [dict(r) for r in rows]


async def create_version(
    key: str, content: str, note: str | None, author: str | None
) -> int:
    """Crea una versión nueva del prompt y la deja ACTIVA. Devuelve el número de versión.

    El prompt (`key`) debe existir ya en la tabla `prompts` (se crea vía seed).
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            exists = await conn.fetchval("SELECT 1 FROM prompts WHERE key = $1", key)
            if not exists:
                raise KeyError(f"prompt '{key}' no existe")

            next_version = await conn.fetchval(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM prompt_versions WHERE key = $1",
                key,
            )
            await conn.execute(
                """
                INSERT INTO prompt_versions (key, version, content, note, author)
                VALUES ($1, $2, $3, $4, $5)
                """,
                key,
                next_version,
                content,
                note,
                author,
            )
            await conn.execute(
                "UPDATE prompts SET active_version = $2, updated_at = now() WHERE key = $1",
                key,
                next_version,
            )

    invalidate(key)
    logger.info("prompt_version_created", key=key, version=next_version, author=author)
    return next_version


async def set_active(key: str, version: int) -> None:
    """Activa una versión existente (rollback / re-activar). Falla si no existe."""
    pool = get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM prompt_versions WHERE key = $1 AND version = $2",
            key,
            version,
        )
        if not exists:
            raise KeyError(f"prompt '{key}' no tiene versión {version}")
        await conn.execute(
            "UPDATE prompts SET active_version = $2, updated_at = now() WHERE key = $1",
            key,
            version,
        )

    invalidate(key)
    logger.info("prompt_version_activated", key=key, version=version)


async def seed_prompt(key: str, description: str, content: str) -> None:
    """Inserta el prompt con su versión 1 activa si aún no existe. Idempotente."""
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            exists = await conn.fetchval("SELECT 1 FROM prompts WHERE key = $1", key)
            if exists:
                return
            await conn.execute(
                "INSERT INTO prompts (key, description, active_version) VALUES ($1, $2, 1)",
                key,
                description,
            )
            await conn.execute(
                """
                INSERT INTO prompt_versions (key, version, content, note, author)
                VALUES ($1, 1, $2, 'seed inicial', 'system')
                """,
                key,
                content,
            )
    logger.info("prompt_seeded", key=key)


async def seed_defaults() -> None:
    """Semilla idempotente de los prompts conocidos desde las constantes Python.

    La constante sigue siendo la fuente inicial; una vez sembrado, la edición vive
    en la DB y las constantes solo actúan de fallback.
    """
    from docbot.commands.user_story import SYSTEM_PROMPT as USER_STORY_PROMPT
    from docbot.rag.prompts import ANSWER_SYSTEM_PROMPT

    await seed_prompt(
        "answer_system",
        "System prompt maestro del agente (respuestas del docbot).",
        ANSWER_SYSTEM_PROMPT,
    )
    await seed_prompt(
        "cmd_user_story",
        "System prompt del comando /user-story (generación de historias de usuario).",
        USER_STORY_PROMPT,
    )
