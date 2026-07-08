"""Reindexa TODO el conocimiento del docbot desde el export unificado de
knowledge-web (vault + catálogo de productos + playbook + autoservicio) en un
solo comando, sin necesidad de levantar la API ni usar el botón de /admin.

Uso (con el venv del proyecto activado, desde la raíz de knowledge-agent):

    python scripts/reindex.py
    python scripts/reindex.py --export-url http://localhost:4321/api/agent/kb-export.json
    python scripts/reindex.py --no-migrations
    python scripts/reindex.py --dry-run     # solo fetch + conteo, sin tocar la DB

Configuración (archivo .env de la raíz):
    DOCBOT_DATABASE_URL      Neon Postgres destino (dev o prod).
    DOCBOT_OPENAI_API_KEY    para generar embeddings.
    DOCBOT_KB_EXPORT_URL     endpoint del export (default apunta a prod si no se pasa --export-url).
    DOCBOT_KB_EXPORT_TOKEN   Bearer del export (== AGENT_EXPORT_TOKEN en knowledge-web).

Qué hace, en orden:
1. Crea el pool de Neon y corre las migraciones SQL idempotentes.
2. Descarga el export unificado de knowledge-web (HTTP GET con Bearer).
3. Por cada documento: upsert incremental por content_hash (saltea lo que no cambió),
   chunking, embeddings y extracción de edges.
4. Borra los "huérfanos" (docs de source=knowledge-web que ya no vienen en el export).
5. Imprime un resumen con docs indexados/sin cambios/borrados, chunks y duración.

Idempotente y seguro: si el fetch del export falla (token/URL mal), aborta ANTES
de tocar la DB (no borra nada).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import pathlib
import sys

# Asegura que `src/` esté en el path cuando se ejecuta como script.
_PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from docbot.config import get_settings  # noqa: E402
from docbot.database import close_pool, create_pool, run_migrations  # noqa: E402
from docbot.indexer.http_source import (  # noqa: E402
    KNOWLEDGE_WEB_SOURCE,
    _fetch_export,
    sync_knowledge_web,
)

# Export del dev server local por defecto (levantá knowledge-web con `npm run dev`).
# Sobreescribir con --export-url o DOCBOT_KB_EXPORT_URL para apuntar a prod.
DEFAULT_EXPORT_URL = "http://localhost:4321/api/agent/kb-export.json"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reindexa el export unificado de knowledge-web en el docbot.",
    )
    parser.add_argument(
        "--export-url",
        default=None,
        help="URL del export. Default: DOCBOT_KB_EXPORT_URL del .env, o localhost:4321.",
    )
    parser.add_argument(
        "--export-token",
        default=None,
        help="Bearer del export. Default: DOCBOT_KB_EXPORT_TOKEN del .env.",
    )
    parser.add_argument(
        "--no-migrations",
        action="store_true",
        help="Omite las migraciones SQL (usar solo si ya se corrieron).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Solo descarga el export y cuenta documentos; no toca la base.",
    )
    return parser.parse_args()


async def main() -> int:
    args = _parse_args()
    settings = get_settings()

    export_url = args.export_url or settings.kb_export_url or DEFAULT_EXPORT_URL
    export_token = args.export_token or settings.kb_export_token or os.getenv("AGENT_EXPORT_TOKEN")

    print(f"[info] DB:            {settings.database_url[:55]}…")
    print(f"[info] Embedding:     {settings.embedding_model}")
    print(f"[info] Export URL:    {export_url}")
    print(f"[info] Export token:  {'configurado' if export_token else 'NO configurado'}")
    print(f"[info] source:        {KNOWLEDGE_WEB_SOURCE}")
    print()

    if not export_token:
        print("[warn] Sin token de export: si el endpoint exige Bearer, dará 401.")

    if args.dry_run:
        docs = await _fetch_export(export_url, export_token)
        by_type: dict[str, int] = {}
        by_repo: dict[str, int] = {}
        for d in docs:
            by_type[d.get("doc_type", "?")] = by_type.get(d.get("doc_type", "?"), 0) + 1
            by_repo[d.get("repo", "?")] = by_repo.get(d.get("repo", "?"), 0) + 1
        print(f"=== Dry-run: {len(docs)} documentos en el export ===")
        print("  por repo:  " + ", ".join(f"{k}={v}" for k, v in sorted(by_repo.items())))
        print("  por tipo:  " + ", ".join(f"{k}={v}" for k, v in sorted(by_type.items())))
        return 0

    pool = await create_pool(settings)
    try:
        if not args.no_migrations:
            print("[info] Ejecutando migraciones SQL…")
            await run_migrations(pool)

        print("[info] Reindexando (fetch + chunk + embeddings + upsert)…")
        result = await sync_knowledge_web(
            pool,
            settings,
            export_url=export_url,
            export_token=export_token,
        )

        print()
        print("=== Reindex completado ===")
        print(f"  docs_indexed:    {result.docs_indexed}")
        print(f"  docs_unchanged:  {result.docs_unchanged}")
        print(f"  docs_deleted:    {result.docs_deleted}")
        print(f"  chunks_created:  {result.chunks_created}")
        print(f"  edges_created:   {result.edges_created}")
        print(f"  duration_secs:   {result.duration_seconds}")
        if result.errors:
            print(f"  errors ({len(result.errors)}):")
            for err in result.errors:
                print(f"    - {err}")
        else:
            print("  errors:          0")

        return 0 if not result.errors else 2
    finally:
        await close_pool()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
