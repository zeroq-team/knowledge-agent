"""Extracción de relaciones (edges) desde frontmatter y wikilinks."""

from __future__ import annotations

import re

import asyncpg
import structlog

from docbot.models import Edge, ParsedDoc

logger = structlog.get_logger(__name__)

_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")


async def _resolve_target(conn: asyncpg.Connection, title: str, repo: str) -> str | None:
    """Busca un doc por título (case-insensitive) dentro del mismo repo o global."""
    row = await conn.fetchrow(
        """
        SELECT id::text FROM docs
        WHERE lower(title) = lower($1)
        ORDER BY (repo = $2)::int DESC
        LIMIT 1
        """,
        title.strip(),
        repo,
    )
    return row["id"] if row else None


def _extract_from_frontmatter(doc: ParsedDoc) -> list[Edge]:
    """Extrae edges desde depends_on y related_services del frontmatter."""
    edges: list[Edge] = []
    fm = doc.frontmatter

    for target in fm.get("depends_on", []) or []:
        edges.append(
            Edge(
                from_doc_path=doc.path,
                to_doc_title=str(target),
                relation_type="depends_on",
                evidence=f"frontmatter.depends_on in {doc.path}",
                confidence=1.0,
            )
        )

    for target in fm.get("related_services", []) or []:
        edges.append(
            Edge(
                from_doc_path=doc.path,
                to_doc_title=str(target),
                relation_type="related_service",
                evidence=f"frontmatter.related_services in {doc.path}",
                confidence=1.0,
            )
        )

    return edges


def _extract_from_wikilinks(doc: ParsedDoc) -> list[Edge]:
    """Extrae edges desde wikilinks [[Target]] en el body."""
    edges: list[Edge] = []
    seen: set[str] = set()

    for match in _WIKILINK_RE.finditer(doc.body):
        target = match.group(1).strip()
        if target.lower() in seen:
            continue
        seen.add(target.lower())

        edges.append(
            Edge(
                from_doc_path=doc.path,
                to_doc_title=target,
                relation_type="related_service",
                evidence=f"wikilink [[{target}]] in {doc.path}",
                confidence=0.7,
            )
        )

    return edges


async def extract_and_persist_edges(
    conn: asyncpg.Connection,
    doc: ParsedDoc,
    doc_id: str,
    repo: str,
) -> int:
    """Extrae edges de un documento y los persiste en la DB.

    Retorna la cantidad de edges creados.
    """
    raw_edges = _extract_from_frontmatter(doc) + _extract_from_wikilinks(doc)

    if not raw_edges:
        return 0

    await conn.execute("DELETE FROM edges WHERE from_doc_id = $1::uuid", doc_id)

    created = 0
    for edge in raw_edges:
        target_id = await _resolve_target(conn, edge.to_doc_title, repo)
        if target_id is None:
            logger.warning(
                "edge_target_not_found",
                from_path=doc.path,
                target=edge.to_doc_title,
                relation=edge.relation_type,
            )
            continue

        await conn.execute(
            """
            INSERT INTO edges (from_doc_id, to_doc_id, relation_type, evidence, confidence)
            VALUES ($1::uuid, $2::uuid, $3, $4, $5)
            """,
            doc_id,
            target_id,
            edge.relation_type,
            edge.evidence,
            edge.confidence,
        )
        created += 1

    return created
