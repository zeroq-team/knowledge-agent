"""Persistencia de conversaciones/mensajes y feedback, más consultas para el panel admin.

Se usa desde `/chat` (registrar el turno + devolver ids), `/feedback` (rating) y las
rutas `/admin/*` (analizar conversaciones y feedback).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import structlog

from docbot.database import get_pool

logger = structlog.get_logger(__name__)


def _valid_uuid(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        return None


async def record_exchange(
    *,
    conversation_id: str | None,
    user_email: str | None,
    user_content: str,
    assistant_content: str,
    citations: list[dict[str, Any]] | None,
    tools_used: list[dict[str, Any]] | None,
    prompt_key: str | None,
    prompt_version: int | None,
    agent_version: str | None,
    command: str | None,
    latency_ms: int | None,
) -> tuple[str, str]:
    """Registra el último turno (user + assistant). Devuelve (conversation_id, assistant_message_id).

    Si `conversation_id` es válido y existe, agrega el turno; si no, crea una conversación
    nueva. Nunca lanza hacia el caller: ante error de DB devuelve ids sintéticos y loguea
    (el chat no debe romperse por telemetría).
    """
    pool = get_pool()
    cid = _valid_uuid(conversation_id)
    citations_json = json.dumps(citations or [])
    tools_json = json.dumps(tools_used or [])

    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                if cid is not None:
                    exists = await conn.fetchval(
                        "SELECT 1 FROM conversations WHERE id = $1", cid
                    )
                    if exists:
                        await conn.execute(
                            "UPDATE conversations SET last_at = now(),"
                            " user_email = COALESCE(user_email, $2) WHERE id = $1",
                            cid,
                            user_email,
                        )
                    else:
                        cid = None

                if cid is None:
                    cid = str(
                        await conn.fetchval(
                            "INSERT INTO conversations (user_email) VALUES ($1) RETURNING id",
                            user_email,
                        )
                    )

                # Turno del usuario (el último mensaje entrante).
                await conn.execute(
                    """
                    INSERT INTO chat_messages (conversation_id, role, content)
                    VALUES ($1, 'user', $2)
                    """,
                    cid,
                    user_content,
                )

                # Turno del assistant, con toda la metadata para el análisis.
                message_id = await conn.fetchval(
                    """
                    INSERT INTO chat_messages (
                        conversation_id, role, content, citations, tools_used,
                        prompt_key, prompt_version, agent_version, command, latency_ms
                    )
                    VALUES ($1, 'assistant', $2, $3::jsonb, $4::jsonb, $5, $6, $7, $8, $9)
                    RETURNING id
                    """,
                    cid,
                    assistant_content,
                    citations_json,
                    tools_json,
                    prompt_key,
                    prompt_version,
                    agent_version,
                    command,
                    latency_ms,
                )
        return cid, str(message_id)
    except Exception:  # noqa: BLE001 — la telemetría nunca debe tumbar el chat
        logger.exception("record_exchange_failed")
        return conversation_id or "", ""


async def set_feedback(
    *, message_id: str, rating: int, comment: str | None, user_email: str | None
) -> bool:
    """Registra/actualiza el rating de un mensaje (👍=1 / 👎=-1). Idempotente por message_id."""
    mid = _valid_uuid(message_id)
    if mid is None or rating not in (1, -1):
        return False
    pool = get_pool()
    exists = await pool.fetchval("SELECT 1 FROM chat_messages WHERE id = $1", mid)
    if not exists:
        return False
    await pool.execute(
        """
        INSERT INTO message_feedback (message_id, rating, comment, user_email)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (message_id) DO UPDATE
            SET rating = EXCLUDED.rating,
                comment = EXCLUDED.comment,
                user_email = EXCLUDED.user_email,
                updated_at = now()
        """,
        mid,
        rating,
        comment,
        user_email,
    )
    logger.info("feedback_recorded", message_id=mid, rating=rating)
    return True


# ---------- Consultas para el panel admin ----------

async def list_conversations(
    *, only_negative: bool = False, q: str | None = None, limit: int = 50, offset: int = 0
) -> list[dict]:
    """Resúmenes de conversaciones para el navegador del panel admin."""
    pool = get_pool()
    conditions: list[str] = []
    params: list[Any] = []

    if q:
        params.append(f"%{q}%")
        conditions.append(
            f"EXISTS (SELECT 1 FROM chat_messages m WHERE m.conversation_id = c.id"
            f" AND m.content ILIKE ${len(params)})"
        )
    if only_negative:
        conditions.append(
            "EXISTS (SELECT 1 FROM chat_messages m JOIN message_feedback f ON f.message_id = m.id"
            " WHERE m.conversation_id = c.id AND f.rating = -1)"
        )

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.extend([limit, offset])

    rows = await pool.fetch(
        f"""
        SELECT
            c.id,
            c.user_email,
            c.started_at,
            c.last_at,
            (SELECT count(*) FROM chat_messages m WHERE m.conversation_id = c.id) AS message_count,
            (SELECT content FROM chat_messages m WHERE m.conversation_id = c.id AND m.role = 'user'
                ORDER BY m.created_at LIMIT 1) AS first_question,
            (SELECT count(*) FROM chat_messages m JOIN message_feedback f ON f.message_id = m.id
                WHERE m.conversation_id = c.id AND f.rating = 1) AS up_count,
            (SELECT count(*) FROM chat_messages m JOIN message_feedback f ON f.message_id = m.id
                WHERE m.conversation_id = c.id AND f.rating = -1) AS down_count
        FROM conversations c
        {where}
        ORDER BY c.last_at DESC
        LIMIT ${len(params) - 1} OFFSET ${len(params)}
        """,
        *params,
    )
    return [dict(r) for r in rows]


async def get_conversation(conversation_id: str) -> dict | None:
    """Hilo completo de una conversación: mensajes con metadata + feedback."""
    cid = _valid_uuid(conversation_id)
    if cid is None:
        return None
    pool = get_pool()
    conv = await pool.fetchrow("SELECT * FROM conversations WHERE id = $1", cid)
    if conv is None:
        return None
    rows = await pool.fetch(
        """
        SELECT
            m.id, m.role, m.content, m.citations, m.tools_used, m.prompt_key,
            m.prompt_version, m.agent_version, m.command, m.latency_ms, m.created_at,
            f.rating AS feedback_rating, f.comment AS feedback_comment
        FROM chat_messages m
        LEFT JOIN message_feedback f ON f.message_id = m.id
        WHERE m.conversation_id = $1
        ORDER BY m.created_at
        """,
        cid,
    )
    messages = []
    for r in rows:
        d = dict(r)
        # citations/tools_used vienen como texto JSON desde asyncpg (columna jsonb).
        for jf in ("citations", "tools_used"):
            if isinstance(d.get(jf), str):
                try:
                    d[jf] = json.loads(d[jf])
                except (ValueError, TypeError):
                    d[jf] = []
        messages.append(d)
    return {**dict(conv), "messages": messages}


async def feedback_stats() -> dict:
    """Totales de feedback y últimos negativos con su conversación."""
    pool = get_pool()
    totals = await pool.fetchrow(
        """
        SELECT
            count(*) FILTER (WHERE rating = 1) AS up,
            count(*) FILTER (WHERE rating = -1) AS down,
            count(*) AS total
        FROM message_feedback
        """
    )
    recent_negative = await pool.fetch(
        """
        SELECT f.message_id, f.comment, f.created_at, m.conversation_id, m.content
        FROM message_feedback f
        JOIN chat_messages m ON m.id = f.message_id
        WHERE f.rating = -1
        ORDER BY f.created_at DESC
        LIMIT 20
        """
    )
    return {
        "up": totals["up"],
        "down": totals["down"],
        "total": totals["total"],
        "recent_negative": [dict(r) for r in recent_negative],
    }
