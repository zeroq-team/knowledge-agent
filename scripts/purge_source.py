"""Purga (opcional) todas las filas de `docs` de una fuente dada.

Contexto: con la ingesta unificada de knowledge-web, TODO el conocimiento
(incluido el vault) entra como ``source="knowledge-web"``. En prod conviene
purgar UNA sola vez las filas viejas del flujo local del vault
(``source="obsidian"``) para no dejar documentos duplicados en el índice.

Los ``doc_chunks`` y ``edges`` se borran en cascada (FK ON DELETE CASCADE).

Uso (con el venv del proyecto activado y DOCBOT_DATABASE_URL en .env):

    python scripts/purge_source.py --source obsidian --yes
    python scripts/purge_source.py --source obsidian        # pide confirmación
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys

_PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from docbot.config import get_settings  # noqa: E402
from docbot.database import close_pool, create_pool  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Purga docs por source.")
    parser.add_argument("--source", required=True, help="Fuente a purgar (ej: obsidian).")
    parser.add_argument(
        "--yes", action="store_true", help="No pedir confirmación interactiva."
    )
    return parser.parse_args()


async def main() -> int:
    args = _parse_args()
    settings = get_settings()
    pool = await create_pool(settings)
    try:
        async with pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT count(*) FROM docs WHERE source = $1", args.source
            )
            print(f"[info] docs con source='{args.source}': {count}")
            if not count:
                return 0
            if not args.yes:
                resp = input(f"¿Borrar {count} docs (y chunks/edges en cascada)? [y/N] ")
                if resp.strip().lower() not in ("y", "yes", "s", "si"):
                    print("[info] Cancelado.")
                    return 1
            await conn.execute("DELETE FROM docs WHERE source = $1", args.source)
            print(f"[ok] {count} docs de source='{args.source}' eliminados.")
        return 0
    finally:
        await close_pool()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
