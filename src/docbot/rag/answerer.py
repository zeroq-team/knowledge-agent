"""Pipeline RAG: búsqueda de chunks relevantes + generación de respuesta con citas."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import asyncpg
import structlog
from openai import AsyncOpenAI

from docbot.config import Settings
from docbot.embeddings import embed_text
from docbot.rag.prompts import ANSWER_SYSTEM_PROMPT, ANSWER_USER_TEMPLATE
from docbot.search.hybrid import SearchResult, hybrid_search

logger = structlog.get_logger(__name__)

_CITATION_RE = re.compile(r"\[(?:\d+\]\s*)?([^\]\[:]+):([^\]#]+)#([^\]]+)\]")


@dataclass
class Citation:
    """Cita extraída de la respuesta del LLM."""

    repo: str
    path: str
    heading: str | None


@dataclass
class UsedChunk:
    """Referencia a un chunk utilizado en la respuesta."""

    doc_id: str
    chunk_id: str
    score: float


@dataclass
class AnswerResult:
    """Resultado completo del pipeline RAG."""

    answer: str
    citations: list[Citation] = field(default_factory=list)
    used_chunks: list[UsedChunk] = field(default_factory=list)


def _format_chunks(chunks: list[SearchResult]) -> str:
    """Formatea chunks como contexto numerado para el prompt."""
    parts: list[str] = []
    for i, c in enumerate(chunks, 1):
        heading_part = f"#{c.heading}" if c.heading else ""
        parts.append(f"[{i}] {c.repo}:{c.path}{heading_part}\n{c.snippet}")
    return "\n\n".join(parts)


def _extract_citations(text: str) -> list[Citation]:
    """Extrae citas del texto de respuesta con regex."""
    citations: list[Citation] = []
    seen: set[str] = set()
    for match in _CITATION_RE.finditer(text):
        key = f"{match.group(1)}:{match.group(2)}#{match.group(3)}"
        if key not in seen:
            seen.add(key)
            citations.append(
                Citation(
                    repo=match.group(1),
                    path=match.group(2),
                    heading=match.group(3),
                )
            )
    return citations


async def generate_answer(
    question: str,
    conn: asyncpg.Connection,
    settings: Settings,
    *,
    source: str | None = None,
    repo: str | None = None,
    doc_type: str | None = None,
) -> AnswerResult:
    """Pipeline completo: embed pregunta → search → LLM → citas."""
    query_embedding = await embed_text(question, settings)

    chunks = await hybrid_search(
        conn,
        query_embedding,
        top_k=settings.rag_max_context_chunks,
        source=source,
        repo=repo,
        doc_type=doc_type,
    )

    if not chunks:
        return AnswerResult(
            answer="No encontré evidencia en la base de conocimiento para responder esta pregunta."
        )

    used = [
        UsedChunk(doc_id=c.doc_id, chunk_id=c.chunk_id, score=c.score)
        for c in chunks
    ]

    chunks_formatted = _format_chunks(chunks)
    user_message = ANSWER_USER_TEMPLATE.format(
        chunks_formatted=chunks_formatted,
        question=question,
    )

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    response = await client.chat.completions.create(
        model=settings.rag_model,
        temperature=settings.rag_temperature,
        messages=[
            {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
    )

    answer_text = response.choices[0].message.content or ""
    citations = _extract_citations(answer_text)

    logger.info(
        "answer_generated",
        question=question[:80],
        chunks_used=len(chunks),
        citations=len(citations),
    )

    return AnswerResult(
        answer=answer_text,
        citations=citations,
        used_chunks=used,
    )
