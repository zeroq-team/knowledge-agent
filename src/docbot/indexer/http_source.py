"""Adaptador de ingesta HTTP: consume el export de knowledge-web e indexa
todo el conocimiento (vault + productos + playbook + autoservicio) en el MISMO
RAG (pgvector `docs`/`doc_chunks`), reutilizando el pipeline existente
(chunker + embeddings + upsert incremental por content_hash + edges).

Contrato del export (implementado en knowledge-web, no se cambia aquí):

    GET {DOCBOT_KB_EXPORT_URL}
    Authorization: Bearer {DOCBOT_KB_EXPORT_TOKEN}
    -> { "documents": ExportDoc[], "count": number }

    ExportDoc = {
        source: "knowledge-web",
        repo: "knowledge" | "products" | "playbook" | "autoservicio",
        path: str, url: str, title: str, doc_type: str,
        frontmatter: object, body: str (markdown), content_hash: str (sha256 hex del body)
    }

Todos los documentos se persisten con ``source="knowledge-web"`` (incluye el
vault). El borrado de huérfanos está *scoped* a ese source (a través de todos
los repos), por lo que NO interfiere con el flujo local ``source="obsidian"``.
"""

from __future__ import annotations

import time

import asyncpg
import httpx
import structlog

from docbot.config import Settings
from docbot.embeddings import embed_texts
from docbot.indexer.chunker import chunk_document
from docbot.indexer.edge_extractor import extract_and_persist_edges
from docbot.indexer.parser import parse_export_doc
from docbot.indexer.sync import _persist_chunks, _upsert_doc
from docbot.models import SyncResult

logger = structlog.get_logger(__name__)

#: Identificador de fuente para todo lo ingerido por este adaptador.
KNOWLEDGE_WEB_SOURCE = "knowledge-web"


async def _fetch_export(url: str, token: str | None, *, timeout: float = 60.0) -> list[dict]:
    """Hace el fetch al endpoint de export y devuelve la lista de documentos."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        payload = resp.json()

    documents = payload.get("documents") or []
    logger.info(
        "kb_export_fetched",
        url=url,
        count=len(documents),
        reported=payload.get("count"),
    )
    return documents


async def _delete_orphans_scoped(
    conn: asyncpg.Connection,
    source: str,
    known_keys: set[tuple[str, str]],
) -> int:
    """Elimina docs de ``source`` (todos los repos) que ya no vienen en el export.

    A diferencia de ``sync._delete_orphans`` (scoped a un repo puntual), acá el
    scope es el ``source`` completo porque el export mezcla varios ``repo``
    (knowledge, products, playbook, autoservicio) bajo una sola fuente.
    """
    rows = await conn.fetch(
        "SELECT id::text, repo, path FROM docs WHERE source = $1",
        source,
    )
    deleted = 0
    for row in rows:
        if (row["repo"], row["path"]) not in known_keys:
            await conn.execute("DELETE FROM docs WHERE id = $1::uuid", row["id"])
            deleted += 1
            logger.info(
                "orphan_deleted",
                source=source,
                repo=row["repo"],
                path=row["path"],
            )
    return deleted


async def sync_knowledge_web(
    pool: asyncpg.Pool,
    settings: Settings,
    *,
    export_url: str | None = None,
    export_token: str | None = None,
) -> SyncResult:
    """Pipeline de indexación del export unificado de knowledge-web.

    Reutiliza chunker + embeddings + ``_upsert_doc``/``_persist_chunks`` con
    incrementalidad por ``content_hash`` y extracción de edges. Todo entra como
    ``source="knowledge-web"``.
    """
    t0 = time.time()
    result = SyncResult()

    url = export_url or settings.kb_export_url
    token = export_token or settings.kb_export_token
    if not url:
        raise ValueError(
            "Falta DOCBOT_KB_EXPORT_URL para indexar el export de knowledge-web."
        )

    documents = await _fetch_export(url, token)
    logger.info("kw_sync_started", source=KNOWLEDGE_WEB_SOURCE, documents=len(documents))

    known_keys: set[tuple[str, str]] = set()

    for raw_doc in documents:
        repo = str(raw_doc.get("repo") or "knowledge-web")

        try:
            parsed = parse_export_doc(raw_doc)
        except Exception as exc:
            path_hint = raw_doc.get("path", "<sin path>")
            logger.warning("export_parse_error", path=path_hint, error=str(exc))
            result.errors.append(f"parse:{path_hint}: {exc}")
            continue

        known_keys.add((repo, parsed.path))

        async with pool.acquire() as conn:
            doc_id, changed = await _upsert_doc(
                conn, KNOWLEDGE_WEB_SOURCE, repo, parsed
            )

            if not changed:
                result.docs_unchanged += 1
                continue

            result.docs_indexed += 1

            chunks = chunk_document(parsed.body, settings)
            if not chunks:
                continue

            try:
                embeddings = await embed_texts([c.content for c in chunks], settings)
            except Exception as exc:
                logger.error("embedding_error", path=parsed.path, error=str(exc))
                result.errors.append(f"embed:{parsed.path}: {exc}")
                continue

            created = await _persist_chunks(conn, doc_id, chunks, embeddings)
            result.chunks_created += created

            edges = await extract_and_persist_edges(conn, parsed, doc_id, repo)
            result.edges_created += edges

    async with pool.acquire() as conn:
        result.docs_deleted = await _delete_orphans_scoped(
            conn, KNOWLEDGE_WEB_SOURCE, known_keys
        )

    result.duration_seconds = round(time.time() - t0, 2)
    logger.info(
        "kw_sync_complete",
        source=KNOWLEDGE_WEB_SOURCE,
        indexed=result.docs_indexed,
        unchanged=result.docs_unchanged,
        deleted=result.docs_deleted,
        chunks=result.chunks_created,
        edges=result.edges_created,
        duration=result.duration_seconds,
    )
    return result
