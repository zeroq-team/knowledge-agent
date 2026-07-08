# Reindexado del conocimiento del Docbot

El Docbot responde desde un índice vectorial en Neon (pgvector). Ese índice se
alimenta del **export unificado de knowledge-web** (vault + catálogo de productos
+ playbook + autoservicio). Cuando cambia el contenido, hay que **reindexar** para
que el agente lo conozca.

Hay dos caminos para reindexar; este documento describe el **local** (un script que
hace todo), que es el recomendado para operar sin depender del botón de `/admin`.

## `scripts/reindex.py` — reindex unificado (recomendado)

Un solo comando: corre migraciones, descarga el export de knowledge-web, chunkea,
genera embeddings, hace upsert incremental (por `content_hash`) y borra huérfanos.

### Requisitos

En el `.env` de la raíz de este repo:

```bash
DOCBOT_DATABASE_URL=postgresql://…neon…      # Neon destino (dev o prod)
DOCBOT_OPENAI_API_KEY=sk-…                    # para embeddings
DOCBOT_KB_EXPORT_TOKEN=<token>                # == AGENT_EXPORT_TOKEN en knowledge-web
# opcional; si no, usa --export-url o el default (localhost:4321):
DOCBOT_KB_EXPORT_URL=http://localhost:4321/api/agent/kb-export.json
```

La URL del export es un endpoint de **knowledge-web**, así que necesitás esa app
corriendo (o apuntar a prod). Para local: en knowledge-web `npm run dev` y asegurate
de que su `.env` tenga `AGENT_EXPORT_TOKEN` (mismo valor que `DOCBOT_KB_EXPORT_TOKEN`).

### Uso

```bash
cd knowledge-agent
source .venv/bin/activate            # o usar .venv/bin/python directamente

# Reindex desde el dev local de knowledge-web (default):
python scripts/reindex.py

# Reindex apuntando a prod (o a otra URL):
python scripts/reindex.py --export-url https://<knowledge-web-prod>/api/agent/kb-export.json

# Ver qué traería el export sin tocar la base (fetch + conteo por repo/tipo):
python scripts/reindex.py --dry-run

# Si las migraciones ya corrieron:
python scripts/reindex.py --no-migrations
```

Salida (resumen): `docs_indexed`, `docs_unchanged`, `docs_deleted`, `chunks_created`,
`edges_created`, `duration_secs`, `errors`.

### Cómo funciona / estrategia

1. **Fuente única de verdad**: el export de knowledge-web (`/api/agent/kb-export.json`).
   El agente no lee Supabase/vault directo; consume el JSON ya normalizado.
2. **Incremental por `content_hash`** (sha256 del body): los docs sin cambios se
   **saltean** (no se re-embeben) → barato y rápido.
3. **Borrado de huérfanos** *scoped* a `source="knowledge-web"`: lo que ya no viene
   en el export se elimina (no afecta el flujo `source="obsidian"` local).
4. **Seguro ante fallos**: si el fetch del export falla (token/URL mal), aborta
   **antes** de tocar la DB — nunca borra por un error de red/credencial.

> Reindexar **no** toca prompts, conversaciones ni feedback: eso es otra parte del
> sistema (ver Docbot Ops en knowledge-web `/admin/docbot`).

## Alternativa: botón "Reindexar docbot" en `/admin` (prod)

Hace lo mismo, pero por HTTP: el botón llama `POST /api/agent/reindex` (knowledge-web),
que a su vez llama `POST {DOCBOT_API_URL}/sync {source:"knowledge-web"}` en el agente,
y este descarga el export y ejecuta el mismo `sync_knowledge_web`. Requiere que el
**agente (Railway)** tenga `DOCBOT_KB_EXPORT_URL` + `DOCBOT_KB_EXPORT_TOKEN`, y
knowledge-web (Vercel) `AGENT_EXPORT_TOKEN` (mismo valor). Sin esas vars, el botón
devuelve 500.

## Otros scripts relacionados

- `scripts/sync_local.py` — indexa **solo el vault local** (`source="obsidian"`,
  `file://<vault>`), sin export unificado. Útil para probar cambios del vault sin
  levantar knowledge-web.
- `scripts/purge_source.py` — elimina todos los docs de un `source` (ej. limpiar un
  índice `obsidian` duplicado). Destructivo; usar con `--yes`.
