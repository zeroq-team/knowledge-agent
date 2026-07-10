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


async def _load_prices(conn) -> dict[str, dict]:
    """Mapa {model: {input_usd_per_1m, output_usd_per_1m}} desde model_prices."""
    try:
        rows = await conn.fetch(
            "SELECT model, input_usd_per_1m, output_usd_per_1m FROM model_prices"
        )
    except Exception:  # noqa: BLE001 — tabla ausente en instancias viejas → sin costo
        return {}
    return {
        r["model"]: {
            "input_usd_per_1m": float(r["input_usd_per_1m"] or 0),
            "output_usd_per_1m": float(r["output_usd_per_1m"] or 0),
        }
        for r in rows
    }


def _cost_of(token_usage: list[dict] | None, prices: dict[str, dict]) -> float:
    """Costo USD de una lista de entradas token_usage cruzada con la tabla de precios.

    Los reasoning_tokens ya están dentro de output_tokens (OpenAI), no se cobran aparte.
    """
    total = 0.0
    for e in token_usage or []:
        p = prices.get(e.get("model", ""), {})
        total += (e.get("input_tokens", 0) or 0) / 1e6 * p.get("input_usd_per_1m", 0.0)
        total += (e.get("output_tokens", 0) or 0) / 1e6 * p.get("output_usd_per_1m", 0.0)
    return round(total, 6)


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
    model: str | None = None,
    token_usage: list[dict[str, Any]] | None = None,
    reasoning: str | None = None,
    steps: list[dict[str, Any]] | None = None,
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
    token_usage_json = json.dumps(token_usage or [])
    steps_json = json.dumps(steps or [])

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
                        prompt_key, prompt_version, agent_version, command, latency_ms, model,
                        token_usage, reasoning, steps
                    )
                    VALUES ($1, 'assistant', $2, $3::jsonb, $4::jsonb, $5, $6, $7, $8, $9, $10,
                            $11::jsonb, $12, $13::jsonb)
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
                    model,
                    token_usage_json,
                    reasoning,
                    steps_json,
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
                WHERE m.conversation_id = c.id AND f.rating = -1) AS down_count,
            (SELECT COALESCE(SUM((e->>'total_tokens')::bigint), 0)
                FROM chat_messages m, jsonb_array_elements(m.token_usage) e
                WHERE m.conversation_id = c.id) AS total_tokens,
            (SELECT COALESCE(SUM(
                    (e->>'input_tokens')::numeric / 1e6 * COALESCE(p.input_usd_per_1m, 0)
                  + (e->>'output_tokens')::numeric / 1e6 * COALESCE(p.output_usd_per_1m, 0)
                ), 0)
                FROM chat_messages m
                CROSS JOIN LATERAL jsonb_array_elements(m.token_usage) e
                LEFT JOIN model_prices p ON p.model = e->>'model'
                WHERE m.conversation_id = c.id) AS cost_usd
        FROM conversations c
        {where}
        ORDER BY c.last_at DESC
        LIMIT ${len(params) - 1} OFFSET ${len(params)}
        """,
        *params,
    )
    return [dict(r) for r in rows]


async def delete_conversation(conversation_id: str) -> bool:
    """Elimina una conversación (cascade a mensajes + feedback). True si borró algo."""
    cid = _valid_uuid(conversation_id)
    if cid is None:
        return False
    pool = get_pool()
    result = await pool.execute("DELETE FROM conversations WHERE id = $1", cid)
    deleted = result.rsplit(" ", 1)[-1] not in ("0", "")
    if deleted:
        logger.info("conversation_deleted", conversation_id=cid)
    return deleted


async def get_conversation(conversation_id: str) -> dict | None:
    """Hilo completo de una conversación: mensajes con metadata + feedback."""
    cid = _valid_uuid(conversation_id)
    if cid is None:
        return None
    pool = get_pool()
    conv = await pool.fetchrow("SELECT * FROM conversations WHERE id = $1", cid)
    if conv is None:
        return None
    async with pool.acquire() as conn:
        prices = await _load_prices(conn)
        rows = await conn.fetch(
            """
            SELECT
                m.id, m.role, m.content, m.citations, m.tools_used, m.prompt_key,
                m.prompt_version, m.agent_version, m.command, m.latency_ms, m.model,
                m.token_usage, m.reasoning, m.steps, m.created_at,
                f.rating AS feedback_rating, f.comment AS feedback_comment
            FROM chat_messages m
            LEFT JOIN message_feedback f ON f.message_id = m.id
            WHERE m.conversation_id = $1
            ORDER BY m.created_at
            """,
            cid,
        )
    messages = []
    conv_tokens = 0
    conv_cost = 0.0
    for r in rows:
        d = dict(r)
        # citations/tools_used/token_usage/steps vienen como texto JSON (columna jsonb).
        for jf in ("citations", "tools_used", "token_usage", "steps"):
            if isinstance(d.get(jf), str):
                try:
                    d[jf] = json.loads(d[jf])
                except (ValueError, TypeError):
                    d[jf] = []
            elif d.get(jf) is None:
                d[jf] = []
        d["cost_usd"] = _cost_of(d.get("token_usage"), prices)
        d["total_tokens"] = sum(e.get("total_tokens", 0) or 0 for e in d.get("token_usage") or [])
        conv_tokens += d["total_tokens"]
        conv_cost += d["cost_usd"]
        messages.append(d)
    return {
        **dict(conv),
        "messages": messages,
        "total_tokens": conv_tokens,
        "cost_usd": round(conv_cost, 6),
    }


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


# ---------- Precios por modelo (editables sin release) ----------

async def list_model_prices() -> list[dict]:
    """Tabla de precios por modelo (USD por 1M de tokens)."""
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT model, input_usd_per_1m, output_usd_per_1m, updated_at"
        " FROM model_prices ORDER BY model"
    )
    return [
        {
            "model": r["model"],
            "input_usd_per_1m": float(r["input_usd_per_1m"] or 0),
            "output_usd_per_1m": float(r["output_usd_per_1m"] or 0),
            "updated_at": r["updated_at"],
        }
        for r in rows
    ]


async def upsert_model_price(
    model: str, input_usd_per_1m: float, output_usd_per_1m: float
) -> dict:
    """Crea o actualiza el precio de un modelo. Devuelve la fila resultante."""
    pool = get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO model_prices (model, input_usd_per_1m, output_usd_per_1m, updated_at)
        VALUES ($1, $2, $3, now())
        ON CONFLICT (model) DO UPDATE
            SET input_usd_per_1m = EXCLUDED.input_usd_per_1m,
                output_usd_per_1m = EXCLUDED.output_usd_per_1m,
                updated_at = now()
        RETURNING model, input_usd_per_1m, output_usd_per_1m, updated_at
        """,
        model,
        input_usd_per_1m,
        output_usd_per_1m,
    )
    logger.info("model_price_upserted", model=model)
    return {
        "model": row["model"],
        "input_usd_per_1m": float(row["input_usd_per_1m"] or 0),
        "output_usd_per_1m": float(row["output_usd_per_1m"] or 0),
        "updated_at": row["updated_at"],
    }


async def usage_stats(days: int = 30) -> dict:
    """Resumen agregado de gasto del docbot: por modelo y totales, en un período.

    `days`: ventana hacia atrás (por created_at del mensaje). 0 = todo el histórico.
    """
    pool = get_pool()
    window = "" if days <= 0 else f"AND m.created_at >= now() - interval '{int(days)} days'"
    rows = await pool.fetch(
        f"""
        SELECT
            e->>'model' AS model,
            SUM((e->>'input_tokens')::bigint)   AS input_tokens,
            SUM((e->>'output_tokens')::bigint)  AS output_tokens,
            SUM((e->>'reasoning_tokens')::bigint) AS reasoning_tokens,
            SUM((e->>'total_tokens')::bigint)   AS total_tokens,
            SUM(
                (e->>'input_tokens')::numeric / 1e6 * COALESCE(p.input_usd_per_1m, 0)
              + (e->>'output_tokens')::numeric / 1e6 * COALESCE(p.output_usd_per_1m, 0)
            ) AS cost_usd
        FROM chat_messages m
        CROSS JOIN LATERAL jsonb_array_elements(m.token_usage) e
        LEFT JOIN model_prices p ON p.model = e->>'model'
        WHERE m.role = 'assistant' {window}
        GROUP BY e->>'model'
        ORDER BY cost_usd DESC NULLS LAST
        """,
    )
    by_model = [
        {
            "model": r["model"],
            "input_tokens": int(r["input_tokens"] or 0),
            "output_tokens": int(r["output_tokens"] or 0),
            "reasoning_tokens": int(r["reasoning_tokens"] or 0),
            "total_tokens": int(r["total_tokens"] or 0),
            "cost_usd": round(float(r["cost_usd"] or 0), 6),
        }
        for r in rows
    ]
    return {
        "days": days,
        "by_model": by_model,
        "total_tokens": sum(m["total_tokens"] for m in by_model),
        "cost_usd": round(sum(m["cost_usd"] for m in by_model), 6),
    }
