"""Indexa un vault local en la BD sin necesidad de levantar la API.

Uso (con el venv del proyecto activado):

    python scripts/sync_local.py
    python scripts/sync_local.py --vault /ruta/al/vault --repo-name knowledge
    python scripts/sync_local.py --no-migrations  # si las migraciones ya corrieron

Lee la configuración desde el archivo .env de la raíz del proyecto
(DOCBOT_DATABASE_URL y DOCBOT_OPENAI_API_KEY).

El script:
1. Crea el pool de Neon Postgres.
2. Corre las migraciones SQL (idempotentes) para garantizar que existan
   las tablas docs, doc_chunks y doc_edges.
3. Llama a sync_repo con repo_url=file://<vault> para evitar git clone.
4. Imprime un resumen con docs indexados, chunks creados y duración.
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys

# Asegura que `src/` esté en el path cuando se ejecuta como script.
_PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from docbot.config import get_settings  # noqa: E402
from docbot.database import close_pool, create_pool, run_migrations  # noqa: E402
from docbot.indexer.sync import sync_repo  # noqa: E402


DEFAULT_VAULT = "/Users/hervispichardo/zeroq/knowledge-web/vault"
DEFAULT_REPO_NAME = "knowledge"
DEFAULT_SOURCE = "obsidian"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Indexa un vault local en la base de conocimiento del docbot.",
    )
    parser.add_argument(
        "--vault",
        default=DEFAULT_VAULT,
        help=f"Ruta absoluta al vault. Default: {DEFAULT_VAULT}",
    )
    parser.add_argument(
        "--repo-name",
        default=DEFAULT_REPO_NAME,
        help=(
            "Nombre lógico del repo que aparecerá en las citas "
            f"(ej: [knowledge:...]). Default: {DEFAULT_REPO_NAME}"
        ),
    )
    parser.add_argument(
        "--source",
        default=DEFAULT_SOURCE,
        help=f"Identificador de la fuente. Default: {DEFAULT_SOURCE}",
    )
    parser.add_argument(
        "--no-migrations",
        action="store_true",
        help="Omite la ejecución de migraciones SQL (usar solo si ya se corrieron).",
    )
    return parser.parse_args()


async def main() -> int:
    args = _parse_args()

    vault_path = pathlib.Path(args.vault).expanduser().resolve()
    if not vault_path.exists() or not vault_path.is_dir():
        print(f"[error] El vault no existe o no es un directorio: {vault_path}")
        return 1

    settings = get_settings()
    print(f"[info] DB: {settings.database_url[:55]}…")
    print(f"[info] Embedding model: {settings.embedding_model}")
    print(f"[info] Vault: {vault_path}")
    print(f"[info] repo_name (citas): {args.repo_name}")
    print()

    pool = await create_pool(settings)
    try:
        if not args.no_migrations:
            print("[info] Ejecutando migraciones SQL…")
            await run_migrations(pool)

        print("[info] Iniciando sync (puede tardar varios minutos en el primer run)…")
        result = await sync_repo(
            pool,
            settings,
            source=args.source,
            repo_url=f"file://{vault_path}",
            repo_name=args.repo_name,
        )

        print()
        print("=== Sync completado ===")
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
