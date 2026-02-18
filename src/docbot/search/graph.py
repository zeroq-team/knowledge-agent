"""Consultas de grafo sobre la tabla edges con CTE recursivos."""

from __future__ import annotations

from dataclasses import dataclass

import asyncpg
import structlog

logger = structlog.get_logger(__name__)


@dataclass
class GraphNode:
    """Nodo en el resultado del grafo."""

    doc_id: str
    title: str
    doc_type: str | None
    criticality: str | None


@dataclass
class GraphEdge:
    """Arista en el resultado del grafo."""

    from_title: str
    to_title: str
    relation_type: str
    evidence: str | None


@dataclass
class ImpactResult:
    """Resultado de un análisis de impacto."""

    nodes: list[GraphNode]
    edges: list[GraphEdge]


async def get_dependents(
    conn: asyncpg.Connection,
    doc_id: str,
    depth: int = 2,
) -> ImpactResult:
    """Obtiene todos los docs que dependen (directa o transitivamente) de doc_id."""
    rows = await conn.fetch(
        """
        WITH RECURSIVE graph AS (
            SELECT
                e.from_doc_id,
                e.to_doc_id,
                e.relation_type,
                e.evidence,
                1 AS depth
            FROM edges e
            WHERE e.to_doc_id = $1::uuid

            UNION ALL

            SELECT
                e.from_doc_id,
                e.to_doc_id,
                e.relation_type,
                e.evidence,
                g.depth + 1
            FROM edges e
            JOIN graph g ON e.to_doc_id = g.from_doc_id
            WHERE g.depth < $2
        )
        SELECT DISTINCT
            g.from_doc_id::text,
            g.to_doc_id::text,
            g.relation_type,
            g.evidence,
            df.title   AS from_title,
            df.doc_type AS from_doc_type,
            df.frontmatter->>'criticality' AS from_criticality,
            dt.title   AS to_title,
            dt.doc_type AS to_doc_type,
            dt.frontmatter->>'criticality' AS to_criticality
        FROM graph g
        JOIN docs df ON df.id = g.from_doc_id
        JOIN docs dt ON dt.id = g.to_doc_id
        """,
        doc_id,
        depth,
    )

    nodes_map: dict[str, GraphNode] = {}
    graph_edges: list[GraphEdge] = []

    for row in rows:
        fid = row["from_doc_id"]
        tid = row["to_doc_id"]

        if fid not in nodes_map:
            nodes_map[fid] = GraphNode(
                doc_id=fid,
                title=row["from_title"],
                doc_type=row["from_doc_type"],
                criticality=row["from_criticality"],
            )
        if tid not in nodes_map:
            nodes_map[tid] = GraphNode(
                doc_id=tid,
                title=row["to_title"],
                doc_type=row["to_doc_type"],
                criticality=row["to_criticality"],
            )

        graph_edges.append(
            GraphEdge(
                from_title=row["from_title"],
                to_title=row["to_title"],
                relation_type=row["relation_type"],
                evidence=row["evidence"],
            )
        )

    return ImpactResult(nodes=list(nodes_map.values()), edges=graph_edges)


async def impact_analysis(
    conn: asyncpg.Connection,
    service_query: str,
    depth: int = 2,
) -> ImpactResult:
    """Analiza impacto: busca un servicio por nombre y obtiene quién depende de él."""
    row = await conn.fetchrow(
        """
        SELECT id::text, title, doc_type, frontmatter->>'criticality' AS criticality
        FROM docs
        WHERE lower(title) LIKE '%' || lower($1) || '%'
        ORDER BY (doc_type = 'service')::int DESC
        LIMIT 1
        """,
        service_query,
    )

    if row is None:
        logger.warning("impact_service_not_found", query=service_query)
        return ImpactResult(nodes=[], edges=[])

    result = await get_dependents(conn, row["id"], depth)

    root = GraphNode(
        doc_id=row["id"],
        title=row["title"],
        doc_type=row["doc_type"],
        criticality=row["criticality"],
    )
    if root.doc_id not in {n.doc_id for n in result.nodes}:
        result.nodes.insert(0, root)

    return result
