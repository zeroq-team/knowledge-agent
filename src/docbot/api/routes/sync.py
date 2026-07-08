"""Endpoint de sincronización / indexación."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from docbot.api.schemas import SyncRequest, SyncResponse
from docbot.config import get_settings
from docbot.indexer.http_source import KNOWLEDGE_WEB_SOURCE, sync_knowledge_web
from docbot.indexer.sync import sync_repo

router = APIRouter()


def _check_sync_auth(request: Request, sync_token: str | None) -> None:
    """Si ``DOCBOT_SYNC_TOKEN`` está definido, exige ``Authorization: Bearer <token>``.

    Si no está definido, no se aplica auth (se preserva el comportamiento actual).
    """
    if not sync_token:
        return
    auth = request.headers.get("Authorization", "")
    expected = f"Bearer {sync_token}"
    if auth != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.post("/sync", response_model=SyncResponse)
async def sync(body: SyncRequest, request: Request) -> SyncResponse:
    """Indexa la knowledge base.

    - ``source="knowledge-web"``: consume el export HTTP unificado (vault +
      productos + playbook + autoservicio) y lo indexa en el mismo RAG.
    - cualquier otro ``source`` (``obsidian``/``gitlab``): clona el repo git de
      ``repo_url`` y parsea sus ``.md`` (flujo clásico).
    """
    settings = get_settings()
    pool = request.app.state.pool

    _check_sync_auth(request, settings.sync_token)

    if body.source == KNOWLEDGE_WEB_SOURCE:
        result = await sync_knowledge_web(pool, settings)
    else:
        if not body.repo_url:
            raise HTTPException(
                status_code=422,
                detail="repo_url es obligatorio para source distinto de 'knowledge-web'.",
            )
        result = await sync_repo(
            pool,
            settings,
            source=body.source,
            repo_url=body.repo_url,
            branch=body.branch,
            repo_name=body.repo_name,
        )

    return SyncResponse(
        docs_indexed=result.docs_indexed,
        docs_unchanged=result.docs_unchanged,
        docs_deleted=result.docs_deleted,
        chunks_created=result.chunks_created,
        edges_created=result.edges_created,
        duration_seconds=result.duration_seconds,
        errors=result.errors,
    )
