"""Búsqueda híbrida: filtros de metadata + similitud vectorial con pgvector."""

from __future__ import annotations

from dataclasses import dataclass

import asyncpg
import structlog

logger = structlog.get_logger(__name__)


@dataclass
class SearchResult:
    """Un resultado de búsqueda con su score y contexto."""

    doc_id: str
    chunk_id: str
    repo: str
    path: str
    heading: str | None
    score: float
    snippet: str
    doc_type: str | None = None


async def hybrid_search(
    conn: asyncpg.Connection,
    query_embedding: list[float],
    *,
    top_k: int = 10,
    source: str | None = None,
    repo: str | None = None,
    doc_type: str | None = None,
    path_prefix: str | None = None,
) -> list[SearchResult]:
    """Ejecuta búsqueda vectorial con filtros opcionales de metadata.

    Combina pre-filtro SQL sobre docs con ranking por cosine similarity
    sobre doc_chunks.embedding.
    """
    from pgvector.asyncpg import register_vector

    await register_vector(conn)

    import numpy as np

    query = """
        SELECT
            d.id::text   AS doc_id,
            c.id::text   AS chunk_id,
            d.repo,
            d.path,
            c.heading,
            1 - (c.embedding <=> $1) AS score,
            c.content    AS snippet,
            d.doc_type
        FROM doc_chunks c
        JOIN docs d ON c.doc_id = d.id
        WHERE ($2::text IS NULL OR d.source = $2)
          AND ($3::text IS NULL OR d.repo = $3)
          AND ($4::text IS NULL OR d.doc_type = $4)
          AND ($5::text IS NULL OR d.path LIKE $5 || '%')
        ORDER BY c.embedding <=> $1
        LIMIT $6
    """

    rows = await conn.fetch(
        query,
        np.array(query_embedding, dtype=np.float32),
        source,
        repo,
        doc_type,
        path_prefix,
        top_k,
    )

    results = [
        SearchResult(
            doc_id=row["doc_id"],
            chunk_id=row["chunk_id"],
            repo=row["repo"],
            path=row["path"],
            heading=row["heading"],
            score=float(row["score"]),
            snippet=row["snippet"],
            doc_type=row["doc_type"],
        )
        for row in rows
    ]

    logger.debug("hybrid_search", results=len(results), top_k=top_k)
    return results
