"""Endpoint RAG: responde preguntas con citas verificables."""

from __future__ import annotations

from fastapi import APIRouter, Request

from docbot.api.schemas import (
    AnswerRequest,
    AnswerResponse,
    CitationItem,
    UsedChunkItem,
)
from docbot.config import get_settings
from docbot.rag.answerer import generate_answer

router = APIRouter()


@router.post("/answer", response_model=AnswerResponse)
async def answer(body: AnswerRequest, request: Request) -> AnswerResponse:
    """Responde una pregunta técnica con citas de la base de conocimiento."""
    settings = get_settings()
    pool = request.app.state.pool

    filters = body.filters

    async with pool.acquire() as conn:
        result = await generate_answer(
            question=body.question,
            conn=conn,
            settings=settings,
            source=getattr(filters, "source", None) if filters else None,
            repo=getattr(filters, "repo", None) if filters else None,
            doc_type=getattr(filters, "doc_type", None) if filters else None,
        )

    return AnswerResponse(
        answer=result.answer,
        citations=[
            CitationItem(repo=c.repo, path=c.path, heading=c.heading)
            for c in result.citations
        ],
        used_chunks=[
            UsedChunkItem(doc_id=u.doc_id, chunk_id=u.chunk_id, score=round(u.score, 4))
            for u in result.used_chunks
        ],
    )
