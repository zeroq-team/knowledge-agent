"""Endpoint de sincronización / indexación."""

from __future__ import annotations

from fastapi import APIRouter, Request

from docbot.api.schemas import SyncRequest, SyncResponse
from docbot.config import get_settings
from docbot.indexer.sync import sync_repo

router = APIRouter()


@router.post("/sync", response_model=SyncResponse)
async def sync(body: SyncRequest, request: Request) -> SyncResponse:
    """Indexa un repo de knowledge base: clona, parsea .md, genera embeddings."""
    settings = get_settings()
    pool = request.app.state.pool

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
