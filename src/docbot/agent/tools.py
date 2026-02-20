"""Tools disponibles para el agente LangGraph."""

from __future__ import annotations

import json
from typing import Annotated

import asyncpg
import structlog
from langchain_core.tools import tool

from docbot.config import Settings
from docbot.embeddings import embed_text
from docbot.search.graph import impact_analysis
from docbot.search.hybrid import hybrid_search

logger = structlog.get_logger(__name__)

_pool: asyncpg.Pool | None = None
_settings: Settings | None = None


def configure_tools(pool: asyncpg.Pool, settings: Settings) -> None:
    """Inyecta pool y settings para que las tools puedan acceder a la DB."""
    global _pool, _settings
    _pool = pool
    _settings = settings


def _require_deps() -> tuple[asyncpg.Pool, Settings]:
    if _pool is None or _settings is None:
        raise RuntimeError("Tools no configuradas. Llama a configure_tools() primero.")
    return _pool, _settings


@tool
async def knowledge_search(
    query: Annotated[str, "Pregunta o términos de búsqueda en lenguaje natural"],
    doc_type: Annotated[str | None, "Filtrar por tipo: service, frontend, infrastructure, policy, procedure. None para todos"] = None,
    top_k: Annotated[int, "Cantidad de resultados a retornar (1-20)"] = 6,
) -> str:
    """Busca en la base de conocimiento de ZeroQ usando búsqueda semántica.

    Usa esta tool para responder preguntas sobre servicios, arquitectura,
    configuración, dependencias, endpoints o cualquier tema documentado.
    Retorna los fragmentos más relevantes con su ubicación exacta.
    """
    pool, settings = _require_deps()
    query_embedding = await embed_text(query, settings)

    async with pool.acquire() as conn:
        results = await hybrid_search(
            conn, query_embedding, top_k=min(top_k, 20), doc_type=doc_type,
        )

    if not results:
        return "No se encontraron resultados relevantes en la base de conocimiento."

    parts: list[str] = []
    for i, r in enumerate(results, 1):
        heading = f"#{r.heading}" if r.heading else ""
        parts.append(
            f"[{i}] {r.repo}:{r.path}{heading} (score: {r.score:.2f})\n{r.snippet}"
        )
    return "\n\n".join(parts)


@tool
async def analyze_impact(
    service_name: Annotated[str, "Nombre del servicio a analizar (ej: turn-o-matic, webapi, Redis)"],
    depth: Annotated[int, "Profundidad del análisis de dependencias (1-3)"] = 2,
) -> str:
    """Analiza el impacto de un servicio: qué otros servicios dependen de él.

    Usa esta tool cuando el usuario pregunte cosas como:
    - "¿Qué pasa si cae Redis/MongoDB/turn-o-matic?"
    - "¿Qué servicios se ven afectados si X deja de funcionar?"
    - "¿Quién depende de este servicio?"

    Retorna el grafo de dependencias con nodos y relaciones.
    """
    pool, _ = _require_deps()

    async with pool.acquire() as conn:
        result = await impact_analysis(conn, service_name, depth=min(depth, 3))

    if not result.nodes:
        return f"No se encontró el servicio '{service_name}' o no tiene dependientes documentados."

    lines = [f"## Análisis de impacto: {service_name}\n"]
    lines.append(f"**Servicios afectados:** {len(result.nodes)}\n")

    for node in result.nodes:
        crit = f" [{node.criticality}]" if node.criticality else ""
        lines.append(f"- **{node.title}** ({node.doc_type or 'unknown'}){crit}")

    if result.edges:
        lines.append("\n**Relaciones:**")
        for edge in result.edges:
            lines.append(f"- {edge.from_title} --[{edge.relation_type}]--> {edge.to_title}")

    return "\n".join(lines)


@tool
async def list_services(
    doc_type: Annotated[str | None, "Filtrar por tipo: service, frontend, infrastructure, policy, procedure. None para todos"] = None,
) -> str:
    """Lista todos los documentos/servicios indexados en la base de conocimiento.

    Usa esta tool cuando el usuario pregunte:
    - "¿Qué servicios hay documentados?"
    - "¿Cuántos documentos tenemos?"
    - "Muéstrame los servicios de tipo frontend"
    """
    pool, _ = _require_deps()

    query = """
        SELECT title, doc_type, path,
               frontmatter->>'criticality' AS criticality,
               frontmatter->>'status' AS status
        FROM docs
        WHERE ($1::text IS NULL OR doc_type = $1)
        ORDER BY doc_type, title
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, doc_type)

    if not rows:
        return "No se encontraron documentos con ese filtro."

    grouped: dict[str, list[str]] = {}
    for row in rows:
        dtype = row["doc_type"] or "sin tipo"
        crit = f" [{row['criticality']}]" if row["criticality"] else ""
        status = f" ({row['status']})" if row["status"] else ""
        entry = f"- **{row['title']}**{crit}{status}"
        grouped.setdefault(dtype, []).append(entry)

    lines = [f"**Total: {len(rows)} documentos**\n"]
    for dtype, entries in grouped.items():
        lines.append(f"\n### {dtype} ({len(entries)})")
        lines.extend(entries)

    return "\n".join(lines)


@tool
async def get_service_detail(
    service_name: Annotated[str, "Nombre del servicio (ej: turn-o-matic, webapi, web-module)"],
) -> str:
    """Obtiene los metadatos completos de un servicio desde su frontmatter.

    Usa esta tool para obtener rápidamente: framework, runtime, criticality,
    dependencias, bases de datos, colas, caché, status y servicios relacionados
    de un servicio específico. Es más rápido que buscar en los chunks.
    """
    pool, _ = _require_deps()

    row = await pool.fetchrow(
        """
        SELECT title, doc_type, path, repo, frontmatter
        FROM docs
        WHERE lower(title) LIKE '%' || lower($1) || '%'
        ORDER BY (doc_type = 'service')::int DESC
        LIMIT 1
        """,
        service_name,
    )

    if row is None:
        return f"No se encontró un servicio con nombre '{service_name}'."

    fm = row["frontmatter"] or {}
    if isinstance(fm, str):
        fm = json.loads(fm)

    lines = [f"## {row['title']}", f"**Tipo:** {row['doc_type']}", f"**Path:** {row['path']}"]

    fields = [
        ("framework", "Framework"),
        ("runtime", "Runtime"),
        ("criticality", "Criticidad"),
        ("status", "Estado"),
        ("uses_database", "Base de datos"),
        ("uses_queue", "Cola de mensajes"),
        ("uses_cache", "Caché"),
    ]
    for key, label in fields:
        if key in fm and fm[key]:
            val = fm[key] if isinstance(fm[key], str) else ", ".join(fm[key])
            lines.append(f"**{label}:** {val}")

    if "depends_on" in fm and fm["depends_on"]:
        deps = fm["depends_on"] if isinstance(fm["depends_on"], list) else [fm["depends_on"]]
        lines.append(f"**Depende de:** {', '.join(deps)}")

    if "related_services" in fm and fm["related_services"]:
        rels = fm["related_services"] if isinstance(fm["related_services"], list) else [fm["related_services"]]
        lines.append(f"**Servicios relacionados:** {', '.join(rels)}")

    return "\n".join(lines)


ALL_TOOLS = [knowledge_search, analyze_impact, list_services, get_service_detail]
