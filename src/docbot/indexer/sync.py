"""Orquestador de indexación: clona repo, parsea .md, genera embeddings, persiste."""

from __future__ import annotations

import pathlib
import shutil
import tempfile
import time
import uuid

import asyncpg
import structlog

from docbot.config import Settings
from docbot.embeddings import embed_texts
from docbot.indexer.chunker import chunk_document
from docbot.indexer.edge_extractor import extract_and_persist_edges
from docbot.indexer.parser import parse_file
from docbot.models import ParsedDoc, SyncResult

logger = structlog.get_logger(__name__)


def _clone_repo(repo_url: str, branch: str) -> pathlib.Path:
    """Clona un repo git a un directorio temporal.  Soporta file:// para paths locales."""
    if repo_url.startswith("file://"):
        return pathlib.Path(repo_url.removeprefix("file://"))

    from git import Repo

    dest = pathlib.Path(tempfile.mkdtemp(prefix="docbot-sync-"))
    logger.info("git_clone", url=repo_url, branch=branch, dest=str(dest))
    Repo.clone_from(repo_url, str(dest), branch=branch, depth=1)
    return dest


def _discover_md_files(root: pathlib.Path) -> list[pathlib.Path]:
    """Lista todos los .md excluyendo .obsidian/ y carpetas ocultas."""
    results: list[pathlib.Path] = []
    for p in root.rglob("*.md"):
        parts = p.relative_to(root).parts
        if any(part.startswith(".") for part in parts):
            continue
        results.append(p)
    return sorted(results)


def _json_serial(obj: object) -> str:
    """Serializa tipos que json.dumps no soporta nativamente (date, datetime)."""
    import datetime

    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")


def _dumps_frontmatter(fm: dict) -> str:
    import json

    return json.dumps(fm, default=_json_serial, ensure_ascii=False)


async def _upsert_doc(
    conn: asyncpg.Connection,
    source: str,
    repo: str,
    parsed: ParsedDoc,
) -> tuple[str, bool]:
    """Inserta o actualiza un doc. Retorna (doc_id, changed)."""

    row = await conn.fetchrow(
        "SELECT id::text, content_hash FROM docs WHERE repo = $1 AND path = $2 AND source = $3",
        repo,
        parsed.path,
        source,
    )

    if row and row["content_hash"] == parsed.content_hash:
        return row["id"], False

    if row:
        await conn.execute(
            """
            UPDATE docs
            SET title = $1, doc_type = $2, frontmatter = $3::jsonb,
                content_hash = $4, updated_at = now()
            WHERE id = $5::uuid
            """,
            parsed.title,
            parsed.doc_type,
            _dumps_frontmatter(parsed.frontmatter),
            parsed.content_hash,
            row["id"],
        )
        return row["id"], True

    new_id = str(uuid.uuid4())
    await conn.execute(
        """
        INSERT INTO docs (id, source, repo, path, title, doc_type, frontmatter, content_hash)
        VALUES ($1::uuid, $2, $3, $4, $5, $6, $7::jsonb, $8)
        """,
        new_id,
        source,
        repo,
        parsed.path,
        parsed.title,
        parsed.doc_type,
        _dumps_frontmatter(parsed.frontmatter),
        parsed.content_hash,
    )
    return new_id, True


async def _persist_chunks(
    conn: asyncpg.Connection,
    doc_id: str,
    chunks: list,
    embeddings: list[list[float]],
) -> int:
    """Borra chunks previos del doc y los reinserta con embeddings."""
    import numpy as np
    from pgvector.asyncpg import register_vector

    await register_vector(conn)
    await conn.execute("DELETE FROM doc_chunks WHERE doc_id = $1::uuid", doc_id)

    for chunk, emb in zip(chunks, embeddings):
        await conn.execute(
            """
            INSERT INTO doc_chunks (doc_id, chunk_index, heading, content, token_count, embedding)
            VALUES ($1::uuid, $2, $3, $4, $5, $6)
            """,
            doc_id,
            chunk.chunk_index,
            chunk.heading,
            chunk.content,
            chunk.token_count,
            np.array(emb, dtype=np.float32),
        )

    return len(chunks)


async def _delete_orphans(
    conn: asyncpg.Connection, source: str, repo: str, known_paths: set[str]
) -> int:
    """Elimina docs que ya no existen en el repo."""
    rows = await conn.fetch(
        "SELECT id::text, path FROM docs WHERE source = $1 AND repo = $2",
        source,
        repo,
    )
    deleted = 0
    for row in rows:
        if row["path"] not in known_paths:
            await conn.execute("DELETE FROM docs WHERE id = $1::uuid", row["id"])
            deleted += 1
            logger.info("orphan_deleted", path=row["path"])
    return deleted


async def sync_repo(
    pool: asyncpg.Pool,
    settings: Settings,
    *,
    source: str = "obsidian",
    repo_url: str,
    branch: str = "main",
    repo_name: str | None = None,
) -> SyncResult:
    """Pipeline completo de indexación de un repo con archivos .md."""
    t0 = time.time()
    result = SyncResult()

    is_local = repo_url.startswith("file://")
    repo_root = _clone_repo(repo_url, branch)
    repo = repo_name or repo_root.name

    try:
        md_files = _discover_md_files(repo_root)
        logger.info("sync_started", repo=repo, files=len(md_files))

        known_paths: set[str] = set()

        for file_path in md_files:
            rel_path = str(file_path.relative_to(repo_root))
            known_paths.add(rel_path)

            try:
                parsed = parse_file(file_path, rel_path)
            except Exception as exc:
                logger.warning("parse_error", path=rel_path, error=str(exc))
                result.errors.append(f"parse:{rel_path}: {exc}")
                continue

            async with pool.acquire() as conn:
                doc_id, changed = await _upsert_doc(conn, source, repo, parsed)

                if not changed:
                    result.docs_unchanged += 1
                    continue

                result.docs_indexed += 1

                chunks = chunk_document(parsed.body, settings)
                if not chunks:
                    continue

                try:
                    embeddings = await embed_texts(
                        [c.content for c in chunks], settings
                    )
                except Exception as exc:
                    logger.error("embedding_error", path=rel_path, error=str(exc))
                    result.errors.append(f"embed:{rel_path}: {exc}")
                    continue

                created = await _persist_chunks(conn, doc_id, chunks, embeddings)
                result.chunks_created += created

                edges = await extract_and_persist_edges(conn, parsed, doc_id, repo)
                result.edges_created += edges

        async with pool.acquire() as conn:
            result.docs_deleted = await _delete_orphans(conn, source, repo, known_paths)

    finally:
        if not is_local and repo_root.exists():
            shutil.rmtree(repo_root, ignore_errors=True)

    result.duration_seconds = round(time.time() - t0, 2)
    logger.info(
        "sync_complete",
        repo=repo,
        indexed=result.docs_indexed,
        unchanged=result.docs_unchanged,
        deleted=result.docs_deleted,
        chunks=result.chunks_created,
        edges=result.edges_created,
        duration=result.duration_seconds,
    )
    return result
