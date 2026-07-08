"""Rutas de administración del docbot: gestión de prompts versionados y análisis
de conversaciones/feedback. Protegidas por Bearer `DOCBOT_ADMIN_TOKEN`.

Consumidas por el panel `/admin/docbot` de knowledge-web (vía proxy que inyecta el token).
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Header, HTTPException, Query

from docbot.api.schemas import PromptActivate, PromptVersionCreate
from docbot.config import get_settings
from docbot.history import store as history_store
from docbot.prompts import store as prompt_store

router = APIRouter(prefix="/admin")
logger = structlog.get_logger(__name__)


def _require_admin(authorization: str | None) -> None:
    """Exige Authorization: Bearer <admin_token> si el token está configurado."""
    admin_token = get_settings().admin_token
    if not admin_token:
        # Sin token configurado, no se exponen las rutas admin.
        raise HTTPException(status_code=503, detail="Admin API no configurada (falta DOCBOT_ADMIN_TOKEN)")
    if authorization != f"Bearer {admin_token}":
        raise HTTPException(status_code=401, detail="Unauthorized")


def _author_from(actor: str | None) -> str | None:
    return actor or None


# ---------- Prompts ----------

@router.get("/prompts")
async def get_prompts(authorization: str | None = Header(default=None)) -> dict:
    _require_admin(authorization)
    return {"prompts": await prompt_store.list_prompts()}


@router.get("/prompts/{key}/versions")
async def get_prompt_versions(
    key: str, authorization: str | None = Header(default=None)
) -> dict:
    _require_admin(authorization)
    return {"key": key, "versions": await prompt_store.list_versions(key)}


@router.post("/prompts/{key}/versions")
async def create_prompt_version(
    key: str,
    body: PromptVersionCreate,
    authorization: str | None = Header(default=None),
    x_zeroq_user: str | None = Header(default=None),
) -> dict:
    _require_admin(authorization)
    try:
        version = await prompt_store.create_version(
            key, body.content, body.note, _author_from(x_zeroq_user)
        )
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"key": key, "version": version, "active": True}


@router.post("/prompts/{key}/activate")
async def activate_prompt_version(
    key: str, body: PromptActivate, authorization: str | None = Header(default=None)
) -> dict:
    _require_admin(authorization)
    try:
        await prompt_store.set_active(key, body.version)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"key": key, "version": body.version, "active": True}


# ---------- Conversaciones ----------

@router.get("/conversations")
async def get_conversations(
    authorization: str | None = Header(default=None),
    only_negative: bool = Query(default=False),
    q: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    _require_admin(authorization)
    return {
        "conversations": await history_store.list_conversations(
            only_negative=only_negative, q=q, limit=limit, offset=offset
        )
    }


@router.get("/conversations/{conversation_id}")
async def get_conversation_detail(
    conversation_id: str, authorization: str | None = Header(default=None)
) -> dict:
    _require_admin(authorization)
    conv = await history_store.get_conversation(conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversación no encontrada")
    return conv


# ---------- Feedback ----------

@router.get("/feedback/stats")
async def get_feedback_stats(authorization: str | None = Header(default=None)) -> dict:
    _require_admin(authorization)
    return await history_store.feedback_stats()
