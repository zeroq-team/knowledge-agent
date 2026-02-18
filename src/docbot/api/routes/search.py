"""Endpoint de búsqueda híbrida."""

from __future__ import annotations

from fastapi import APIRouter, Request

from docbot.api.schemas import SearchRequest, SearchResponse, SearchResultItem
from docbot.config import get_settings
from docbot.embeddings import embed_text
from docbot.search.hybrid import hybrid_search

router = APIRouter()


@router.post("/search", response_model=SearchResponse)
async def search(body: SearchRequest, request: Request) -> SearchResponse:
    """Búsqueda híbrida: filtros de metadata + similitud vectorial."""
    settings = get_settings()
    pool = request.app.state.pool

    query_embedding = await embed_text(body.query, settings)

    filters = body.filters or type(body.filters)()  # type: ignore[arg-type]

    async with pool.acquire() as conn:
        results = await hybrid_search(
            conn,
            query_embedding,
            top_k=body.top_k,
            source=getattr(filters, "source", None),
            repo=getattr(filters, "repo", None),
            doc_type=getattr(filters, "doc_type", None),
            path_prefix=getattr(filters, "path_prefix", None),
        )

    items = [
        SearchResultItem(
            doc_id=r.doc_id,
            chunk_id=r.chunk_id,
            repo=r.repo,
            path=r.path,
            heading=r.heading,
            score=round(r.score, 4),
            snippet=r.snippet[:500],
        )
        for r in results
    ]

    return SearchResponse(results=items, total=len(items))
