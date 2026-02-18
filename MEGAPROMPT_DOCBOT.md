# ZeroQ Docbot — Megaprompt de Generación Completa

> **Uso**: Alimentar este documento completo a un agente de IA (Cursor, Claude, GPT) para generar el proyecto ZeroQ Docbot de principio a fin. Cada sección está diseñada con patrones de prompt engineering para maximizar la precisión y consistencia del output.

---

## § ROLE — Identidad del Agente Generador

```
Eres un arquitecto senior de software backend especializado en:
- Python 3.11+, FastAPI, SQLAlchemy/asyncpg
- Sistemas RAG (Retrieval-Augmented Generation) con embeddings y pgvector
- Grafos de conocimiento modelados sobre PostgreSQL
- CI/CD con GitLab CI
- Diseño de CLIs con Typer
- Documentación técnica en Markdown/Obsidian

Tu misión: generar el proyecto completo "ZeroQ Docbot" como un mono-repo Python 
listo para producción, siguiendo ESTRICTAMENTE la especificación que sigue.

REGLAS FUNDAMENTALES:
1. NO inventes dependencias no mencionadas en la spec.
2. NO generes código placeholder o "TODO" — todo debe ser funcional.
3. Cada archivo debe tener docstrings en español que expliquen su propósito.
4. Prefiere asyncio consistentemente (asyncpg, httpx async, etc.).
5. Toda respuesta del LLM DEBE incluir citas verificables (repo:path#Heading).
6. Sigue PEP 8, usa type hints en TODAS las funciones.
7. Genera tests unitarios para componentes críticos.
```

---

## § CONTEXT — Qué es ZeroQ Docbot

ZeroQ Docbot es un agente interno que consume dos fuentes de conocimiento:

| Fuente | Formato | Ejemplo |
|--------|---------|---------|
| **Obsidian Vault** (repo knowledge) | Markdown + YAML frontmatter | `services/ticket-api.md` con `depends_on: [rabbitmq, postgres]` |
| **Repos Producto** (código) | README.md + Helm charts | `charts/values.yaml` con env vars de conexión |

Con esa información, el docbot puede:
- **Responder preguntas** con citas exactas (RAG)
- **Explicar arquitectura** y dependencias (Knowledge Graph sobre Postgres)
- **Analizar impacto** ("si cae RabbitMQ, qué servicios se afectan")
- **Auditar documentación** ("servicios críticos sin sección DRP")
- **Generar Context Packs** por repo (`.zeroq-context/*.md`)

---

## § ARCHITECTURE — Estructura del Proyecto

Genera esta estructura de directorios EXACTA:

```
zeroq-docbot/
├── README.md
├── pyproject.toml
├── .env.example
├── .gitignore
├── .gitlab-ci.yml
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
├── db/
│   └── migrations/
│       ├── 001_extensions.sql
│       ├── 002_tables.sql
│       └── 003_indexes.sql
├── src/
│   └── docbot/
│       ├── __init__.py
│       ├── config.py              # Settings con pydantic-settings
│       ├── models.py              # SQLAlchemy / Pydantic models
│       ├── database.py            # Connection pool asyncpg
│       ├── embeddings.py          # Cliente OpenAI embeddings
│       ├── indexer/
│       │   ├── __init__.py
│       │   ├── parser.py          # Parse markdown + frontmatter
│       │   ├── chunker.py         # Chunking por headings + tokens
│       │   ├── edge_extractor.py  # Extrae relaciones → edges
│       │   ├── helm_parser.py     # Parse Helm charts
│       │   └── sync.py            # Orquestador de indexación
│       ├── search/
│       │   ├── __init__.py
│       │   ├── hybrid.py          # Búsqueda híbrida (metadata + vector)
│       │   └── graph.py           # Consultas de grafo (impact, dependencies)
│       ├── rag/
│       │   ├── __init__.py
│       │   ├── answerer.py        # RAG pipeline con citas
│       │   └── prompts.py         # Templates de prompts
│       ├── audit/
│       │   ├── __init__.py
│       │   └── rules.py           # Reglas de auditoría (missing_drp, etc.)
│       ├── context_pack/
│       │   ├── __init__.py
│       │   └── generator.py       # Generador de .zeroq-context/
│       ├── api/
│       │   ├── __init__.py
│       │   ├── app.py             # FastAPI app factory
│       │   ├── routes/
│       │   │   ├── __init__.py
│       │   │   ├── health.py
│       │   │   ├── search.py
│       │   │   ├── answer.py
│       │   │   ├── impact.py
│       │   │   ├── audit.py
│       │   │   ├── context_pack.py
│       │   │   └── sync.py
│       │   └── schemas.py         # Request/Response Pydantic models
│       └── cli/
│           ├── __init__.py
│           └── main.py            # Typer CLI
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_parser.py
│   ├── test_chunker.py
│   ├── test_edge_extractor.py
│   ├── test_search.py
│   ├── test_answerer.py
│   └── test_audit.py
└── docs/
    ├── operations.md              # Cómo reindex, agregar repos, crear reglas
    └── architecture.md            # Diagrama de componentes
```

---

## § DATABASE — Esquema SQL (Neon + pgvector)

Genera las migraciones SQL exactamente así:

### `001_extensions.sql`

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
```

### `002_tables.sql`

```sql
CREATE TABLE docs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source      TEXT NOT NULL CHECK (source IN ('obsidian', 'gitlab')),
    repo        TEXT NOT NULL,
    path        TEXT NOT NULL,
    title       TEXT NOT NULL,
    doc_type    TEXT NOT NULL,  -- service|feature|policy|procedure|readme|helm|...
    frontmatter JSONB DEFAULT '{}',
    content_hash TEXT NOT NULL,
    updated_at  TIMESTAMPTZ DEFAULT now(),
    UNIQUE (repo, path, source)
);

CREATE TABLE doc_chunks (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    doc_id      UUID NOT NULL REFERENCES docs(id) ON DELETE CASCADE,
    chunk_index INT NOT NULL,
    heading     TEXT,
    content     TEXT NOT NULL,
    token_count INT NOT NULL,
    embedding   vector(1536),
    UNIQUE (doc_id, chunk_index)
);

CREATE TABLE edges (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    from_doc_id    UUID NOT NULL REFERENCES docs(id) ON DELETE CASCADE,
    to_doc_id      UUID NOT NULL REFERENCES docs(id) ON DELETE CASCADE,
    relation_type  TEXT NOT NULL,  
    -- depends_on|related_service|integrates_with|uses_db|uses_queue|uses_cache|publishes_event|consumes_event
    evidence       TEXT,           -- path + heading o snippet de origen
    confidence     REAL DEFAULT 1.0,
    updated_at     TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE repo_context (
    repo               TEXT PRIMARY KEY,
    generated_at       TIMESTAMPTZ DEFAULT now(),
    ecosystem_md       TEXT,
    this_repo_role_md  TEXT,
    dependencies_md    TEXT,
    impact_md          TEXT,
    runbooks_md        TEXT,
    contracts_md       TEXT,
    evidence_links_md  TEXT
);
```

### `003_indexes.sql`

```sql
CREATE INDEX idx_chunks_embedding ON doc_chunks 
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX idx_docs_source_repo_type ON docs (source, repo, doc_type);

CREATE INDEX idx_docs_frontmatter ON docs USING GIN (frontmatter);

CREATE INDEX idx_edges_from ON edges (from_doc_id);
CREATE INDEX idx_edges_to ON edges (to_doc_id);
CREATE INDEX idx_edges_type ON edges (relation_type);
```

---

## § CONFIG — Configuración con pydantic-settings

`src/docbot/config.py` debe exponer:

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Base de datos
    database_url: str  # postgresql+asyncpg://...
    
    # OpenAI
    openai_api_key: str
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    
    # Chunking
    chunk_target_tokens: int = 750
    chunk_min_tokens: int = 200
    chunk_max_tokens: int = 900
    
    # Search
    search_top_k: int = 10
    similarity_threshold: float = 0.7
    
    # RAG
    rag_model: str = "gpt-4o-mini"  # Modelo para respuestas
    rag_max_context_chunks: int = 8
    rag_temperature: float = 0.1
    
    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    
    # Docbot API URL (para CLI)
    docbot_api_url: str = "http://localhost:8000"
    
    model_config = {"env_prefix": "DOCBOT_", "env_file": ".env"}
```

El `.env.example` debe contener todas las variables con valores de ejemplo seguros (sin secretos reales).

---

## § INDEXER — Módulo de Indexación

### Chain-of-Thought para el Indexer

El indexer sigue este flujo secuencial (impleméntalo así):

```
1. CLONAR/PULL → Obtener repo (o usar path local)
2. DESCUBRIR   → Listar archivos .md (y Helm si aplica)
3. HASHEAR     → Calcular SHA-256 de cada archivo
4. FILTRAR     → Comparar hash con docs.content_hash → solo procesar cambios
5. PARSEAR     → Extraer frontmatter YAML + contenido markdown
6. CHUNKEAR    → Split por headings (#, ##, ###), respetar límite de tokens
7. EMBEDDINGS  → Llamar OpenAI batch embeddings (text-embedding-3-small)
8. PERSISTIR   → Upsert docs + delete/insert chunks + insert embeddings
9. EXTRAER EDGES → Desde frontmatter + wikilinks + Helm signals
10. LIMPIAR    → Detectar docs huérfanos (archivos eliminados) y borrar
```

### `parser.py` — Reglas de Parseo

```python
"""
Responsabilidades:
- Leer archivo markdown
- Si inicia con '---', parsear YAML frontmatter hasta el siguiente '---'
- Retornar: ParsedDoc(path, title, frontmatter: dict, body: str, content_hash: str)

Usar: python-frontmatter para parseo
Usar: hashlib.sha256 para content_hash sobre el contenido COMPLETO del archivo
"""
```

### `chunker.py` — Reglas de Chunking

```python
"""
Estrategia de chunking:
1. Split por headings de nivel 1-3 (regex: r'^#{1,3}\s+')
2. Cada chunk hereda el heading bajo el que cae
3. Si un chunk excede chunk_max_tokens (900), subdividir por párrafos (\n\n)
4. Si un chunk es menor que chunk_min_tokens (200) y NO es el último, 
   fusionarlo con el siguiente
5. Calcular token_count con tiktoken (encoding: cl100k_base)

Retornar: list[Chunk(heading, content, token_count, chunk_index)]
"""
```

### `edge_extractor.py` — Reglas de Extracción de Relaciones

```python
"""
Fuentes de relaciones y su mapeo:

1. frontmatter['depends_on'] → edge(relation_type='depends_on', confidence=1.0)
2. frontmatter['related_services'] → edge(relation_type='related_service', confidence=1.0)
3. wikilinks [[ServiceName]] en body → edge(relation_type='related_service', confidence=0.7)
4. Desde Helm charts:
   - env var con 'DATABASE_URL' o 'POSTGRES' → edge('uses_db', confidence=0.9)
   - env var con 'REDIS' → edge('uses_cache', confidence=0.9)
   - env var con 'RABBITMQ' o 'AMQP' → edge('uses_queue', confidence=0.9)
   - env var con 'KAFKA' → edge('uses_queue', confidence=0.9)

Para resolver targets: buscar en tabla docs por title ILIKE '%target%'
Si no se encuentra target doc, registrar warning en logs pero NO crear edge roto.

Evidence format: "frontmatter.depends_on in {path}" o "wikilink in {path}#{heading}"
"""
```

### `helm_parser.py` — Parse de Helm Charts

```python
"""
Archivos a procesar:
- Chart.yaml → extraer name, version, description, dependencies
- values.yaml → extraer env vars, ports, replicas, HPA, resources
- templates/*.yaml → extraer service names, ingress hosts

Retornar: HelmInfo(
    chart_name, version, description,
    env_vars: list[EnvVar(name, value_hint)],
    services: list[ServicePort(name, port, protocol)],
    dependencies: list[HelmDep(name, version, repository)],
    replicas: int | None,
    hpa: HpaConfig | None
)

Guardar como doc con doc_type='helm', frontmatter con los datos estructurados.
"""
```

### `sync.py` — Orquestador

```python
"""
Función principal: async def sync_repo(source, repo_url, branch, scope) -> SyncResult

SyncResult(
    docs_indexed: int,
    docs_unchanged: int,
    docs_deleted: int, 
    chunks_created: int,
    edges_created: int,
    duration_seconds: float
)

Flujo:
1. Clonar repo a /tmp/docbot-sync-{uuid} (o usar path local si es file://)
2. Filtrar archivos según scope:
   - 'obsidian': **/*.md excepto .obsidian/**
   - 'readme': README.md
   - 'helm': charts/**/*, Chart.yaml, values.yaml, templates/**
   - 'all': todo lo anterior
3. Para cada archivo: parse → check hash → chunk → embed → persist
4. Extraer edges de todos los docs nuevos/actualizados
5. Limpiar docs huérfanos (en DB pero no en repo)
6. Retornar SyncResult
"""
```

---

## § SEARCH — Búsqueda Híbrida

### `hybrid.py`

```python
"""
Implementar búsqueda híbrida que combine:

1. FILTRO POR METADATA (pre-filtro SQL):
   - source, repo, doc_type: filtro exacto
   - tags: filtro en frontmatter->>'tags' (jsonb)
   - path_prefix: docs.path LIKE '{prefix}%'

2. SIMILITUD VECTORIAL (pgvector):
   - Generar embedding de la query
   - Buscar los top_k chunks más similares con cosine distance
   - WHERE 1 - (embedding <=> query_embedding) >= similarity_threshold

3. COMBINAR:
   - Aplicar filtros de metadata primero (eficiente)
   - Luego ranking por similitud vectorial
   - Retornar: list[SearchResult(repo, path, heading, score, snippet)]

Query SQL aproximada:
    SELECT d.repo, d.path, c.heading,
           1 - (c.embedding <=> $1::vector) AS score,
           c.content AS snippet
    FROM doc_chunks c
    JOIN docs d ON c.doc_id = d.id
    WHERE ($2::text IS NULL OR d.source = $2)
      AND ($3::text IS NULL OR d.repo = $3)
      AND ($4::text IS NULL OR d.doc_type = $4)
    ORDER BY c.embedding <=> $1::vector
    LIMIT $5
"""
```

### `graph.py`

```python
"""
Consultas de grafo sobre la tabla edges:

1. get_dependencies(doc_id, depth=1..3):
   - BFS/CTE recursivo sobre edges
   - Retorna nodos y aristas hasta profundidad N
   
2. get_dependents(doc_id, depth=1..3):
   - BFS inverso: quién depende de este servicio
   
3. impact_analysis(service_query, depth=2):
   - Buscar doc por nombre/query
   - Obtener dependents recursivos
   - Enriquecer con doc.frontmatter['criticality'] si existe
   - Retornar: ImpactResult(nodes, edges)

CTE recursivo ejemplo:
    WITH RECURSIVE graph AS (
        SELECT from_doc_id, to_doc_id, relation_type, evidence, 1 AS depth
        FROM edges WHERE from_doc_id = $1
        UNION ALL
        SELECT e.from_doc_id, e.to_doc_id, e.relation_type, e.evidence, g.depth + 1
        FROM edges e JOIN graph g ON e.from_doc_id = g.to_doc_id
        WHERE g.depth < $2
    )
    SELECT * FROM graph
"""
```

---

## § RAG — Pipeline de Respuestas con Citas

### `prompts.py` — Templates de Prompts

```python
ANSWER_SYSTEM_PROMPT = """Eres ZeroQ Docbot, un asistente técnico interno de ZeroQ.
Respondes EXCLUSIVAMENTE con información de la base de conocimiento proporcionada.

REGLAS ESTRICTAS:
1. Responde SOLO con información de los chunks de contexto proporcionados.
2. SIEMPRE incluye citas en formato [repo:path#Heading] después de cada afirmación.
3. Si el contexto no contiene la respuesta, di exactamente: 
   "No encontré evidencia en la base de conocimiento para responder esta pregunta."
4. Mínimo 2 citas si hay resultados relevantes.
5. No inventes información. No extrapoles más allá de lo que dicen los chunks.
6. Responde en español.
7. Si hay contradicciones entre fuentes, señálalas explícitamente.

FORMATO DE CITA: [repo:path#Heading]
Ejemplo: [knowledge:services/ticket-api.md#Arquitectura]
"""

ANSWER_USER_TEMPLATE = """Contexto recuperado de la base de conocimiento:

{chunks_formatted}

---

Pregunta del usuario: {question}

Responde citando las fuentes relevantes."""

IMPACT_SYSTEM_PROMPT = """Eres ZeroQ Docbot realizando un análisis de impacto.
Se te proporciona un grafo de dependencias y documentación de los servicios involucrados.

Tu tarea:
1. Listar todos los servicios afectados directa e indirectamente.
2. Para cada servicio, indicar el tipo de afectación y la criticidad.
3. Citar la evidencia de cada relación.
4. Sugerir acciones de mitigación si la documentación las menciona.
"""

CONTEXT_PACK_SYSTEM_PROMPT = """Eres ZeroQ Docbot generando un Context Pack para un repositorio.
Un Context Pack es un conjunto de archivos markdown que proporcionan contexto 
a agentes de código (Cursor, Copilot) sobre el rol del repo en el ecosistema.

Genera EXACTAMENTE estas secciones como archivos markdown separados:
1. ecosystem.md — Visión general del ecosistema y cómo encaja este repo
2. this-repo.md — Rol específico, responsabilidades, ownership
3. dependencies.md — De qué depende y por qué
4. impact.md — Qué se rompe si este repo falla
5. runbooks.md — Procedimientos operativos relevantes
6. contracts.md — APIs expuestas y consumidas, eventos, schemas
7. evidence-links.md — Links a la documentación fuente con citas

Basa TODO en los chunks y el grafo proporcionado. No inventes información.
"""
```

### `answerer.py` — Pipeline RAG

```python
"""
Flujo de answer(question, filters):

1. Generar embedding de la pregunta
2. Buscar chunks relevantes con hybrid_search(query_embedding, filters, top_k=8)
3. Formatear chunks como contexto numerado:
   [1] repo:path#heading
   {content}
   
   [2] repo:path#heading  
   {content}
   ...
4. Llamar al LLM con ANSWER_SYSTEM_PROMPT + ANSWER_USER_TEMPLATE
5. Parsear respuesta y extraer citas mencionadas
6. Validar que las citas correspondan a chunks reales del contexto
7. Retornar: AnswerResult(answer, citations, used_chunks)

IMPORTANTE: Si el LLM genera una cita que no corresponde a ningún chunk, 
marcarla como 'unverified' en el response.
"""
```

---

## § AUDIT — Reglas de Auditoría

### `rules.py`

```python
"""
Reglas implementadas como funciones puras que consultan la DB:

1. missing_drp:
   - Buscar docs con doc_type='service' y frontmatter->>'criticality' IN ('high', 'critical')
   - Verificar que algún chunk tenga heading ILIKE '%DRP%' o '%Disaster Recovery%'
   - Retornar los que NO tienen esa sección

2. missing_owner:
   - Buscar docs con doc_type='service'
   - Verificar frontmatter ? 'owner' (tiene key owner)
   - Retornar los que NO tienen owner definido

3. missing_monitoring:
   - Buscar docs con doc_type='service'
   - Verificar que algún chunk tenga heading ILIKE '%monitoring%' o '%observabilidad%' o '%alertas%'
   - Retornar los que NO tienen

Cada regla retorna: list[AuditItem(repo, path, title, rule, detail)]

Diseñar para extensibilidad: usar un registry de reglas donde se puedan agregar nuevas
sin modificar código existente (patrón Strategy o simplemente un dict de funciones).
"""
```

---

## § API — FastAPI Endpoints

### `app.py` — App Factory

```python
"""
- Usar lifespan context manager para inicializar/cerrar pool de DB
- Registrar routers de cada módulo
- Middleware de logging estructurado (structlog)
- Middleware de timing (request duration)
- Manejo global de errores con responses consistentes
"""
```

### Schemas — Request/Response Models (Pydantic v2)

Genera TODOS estos modelos en `schemas.py`:

```python
"""
# Request Models

class SearchRequest(BaseModel):
    query: str
    filters: SearchFilters | None = None
    top_k: int = Field(default=10, ge=1, le=50)

class SearchFilters(BaseModel):
    source: str | None = None          # 'obsidian' | 'gitlab'
    repo: str | None = None
    doc_type: str | None = None
    tags: list[str] | None = None
    path_prefix: str | None = None

class AnswerRequest(BaseModel):
    question: str = Field(min_length=5)
    filters: SearchFilters | None = None

class ImpactRequest(BaseModel):
    service: str
    change_description: str | None = None
    depth: int = Field(default=2, ge=1, le=3)

class ListMissingRequest(BaseModel):
    rule: str  # 'missing_drp' | 'missing_owner' | 'missing_monitoring'

class ContextPackRequest(BaseModel):
    repo: str

class SyncRequest(BaseModel):
    source: str          # 'obsidian' | 'gitlab'
    repo_url: str
    branch: str = "main"
    scope: str = "all"   # 'obsidian' | 'readme' | 'helm' | 'all'

# Response Models

class SearchResult(BaseModel):
    repo: str
    path: str
    heading: str | None
    score: float
    snippet: str

class SearchResponse(BaseModel):
    results: list[SearchResult]
    total: int
    query_embedding_ms: float
    search_ms: float

class Citation(BaseModel):
    repo: str
    path: str
    heading: str | None

class UsedChunk(BaseModel):
    doc_id: str
    chunk_id: str
    score: float

class AnswerResponse(BaseModel):
    answer: str
    citations: list[Citation]
    used_chunks: list[UsedChunk]

class ImpactNode(BaseModel):
    service: str
    doc_type: str | None
    criticality: str | None

class ImpactEdge(BaseModel):
    from_service: str
    to_service: str
    relation_type: str
    evidence: str | None

class ImpactResponse(BaseModel):
    nodes: list[ImpactNode]
    edges: list[ImpactEdge]

class AuditItem(BaseModel):
    repo: str
    path: str
    title: str
    rule: str
    detail: str | None = None

class ListMissingResponse(BaseModel):
    rule: str
    items: list[AuditItem]
    total: int

class SyncResponse(BaseModel):
    docs_indexed: int
    docs_unchanged: int
    docs_deleted: int
    chunks_created: int
    edges_created: int
    duration_seconds: float

class HealthResponse(BaseModel):
    status: str  # 'ok' | 'degraded' | 'error'
    db_connected: bool
    version: str
"""
```

### Ejemplo de Endpoint (Few-Shot para el agente)

Así debe verse el endpoint `/answer`:

```python
@router.post("/answer", response_model=AnswerResponse)
async def answer_question(request: AnswerRequest, db=Depends(get_db)):
    """Responde una pregunta técnica con citas verificables de la base de conocimiento."""
    # 1. Generar embedding de la pregunta
    query_embedding = await embed_text(request.question)
    
    # 2. Búsqueda híbrida
    chunks = await hybrid_search(
        db, query_embedding, 
        filters=request.filters,
        top_k=settings.rag_max_context_chunks
    )
    
    # 3. Si no hay chunks relevantes, retornar respuesta vacía
    if not chunks:
        return AnswerResponse(
            answer="No encontré evidencia en la base de conocimiento para responder esta pregunta.",
            citations=[],
            used_chunks=[]
        )
    
    # 4. Generar respuesta con RAG
    result = await generate_answer(request.question, chunks)
    
    return result
```

---

## § CLI — Comandos Typer

### `cli/main.py`

```python
"""
Comandos a implementar:

1. docbot search "<query>" [--type TYPE] [--repo REPO] [--source SOURCE] [--top-k N]
   → Llama POST /search, muestra tabla con resultados

2. docbot explain "<query>"
   → Llama POST /answer, muestra respuesta + citas formateadas

3. docbot impact --service SERVICE [--change "descripción"] [--depth N]
   → Llama POST /impact, muestra grafo en formato texto (árbol)

4. docbot list-missing --rule RULE
   → Llama POST /list_missing, muestra tabla de items faltantes

5. docbot gen-context --repo REPO [--output DIR]
   → Llama POST /generate_context_pack, escribe archivos .md en output dir

6. docbot sync --source SOURCE --repo-url URL [--branch BRANCH] [--scope SCOPE]
   → Llama POST /sync, muestra resultados de indexación

Usar rich para formatear output con tablas, colores y paneles.
Usar httpx para llamadas async al API.
Cada comando debe manejar errores de red y API gracefully.
"""
```

---

## § GITLAB CI — Pipelines

### `.gitlab-ci.yml`

```yaml
# Generar DOS jobs principales:

# 1. Para el repo knowledge (Obsidian):
# Trigger: merge a main
# Acción: Llamar /sync con source=obsidian

# 2. Para repos producto:
# Trigger: merge a main
# Acción: 
#   a) Llamar /sync con source=gitlab (README + helm)
#   b) Llamar /generate_context_pack
#   c) Crear branch, commit .zeroq-context/*.md, abrir MR

# Variables CI esperadas:
# DOCBOT_API_URL: URL del API
# DOCBOT_SYNC_TOKEN: (futuro, por ahora sin auth)
# CI_PROJECT_URL: auto de GitLab
# CI_DEFAULT_BRANCH: auto de GitLab
```

Genera el `.gitlab-ci.yml` completo con stages, jobs y scripts ejecutables.

---

## § DEPENDENCIES — pyproject.toml

```toml
[project]
name = "zeroq-docbot"
version = "0.1.0"
description = "RAG + Knowledge Graph agent para documentación interna de ZeroQ"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "pydantic>=2.9.0",
    "pydantic-settings>=2.5.0",
    "asyncpg>=0.30.0",
    "pgvector>=0.3.0",
    "openai>=1.50.0",
    "python-frontmatter>=1.1.0",
    "tiktoken>=0.7.0",
    "typer[all]>=0.12.0",
    "httpx>=0.27.0",
    "rich>=13.9.0",
    "structlog>=24.4.0",
    "pyyaml>=6.0.0",
    "gitpython>=3.1.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3.0",
    "pytest-asyncio>=0.24.0",
    "pytest-cov>=5.0.0",
    "ruff>=0.6.0",
]

[project.scripts]
docbot = "docbot.cli.main:app"

[tool.ruff]
target-version = "py311"
line-length = 100

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

**NO agregues dependencias no listadas aquí.** Si necesitas algo adicional justifícalo en un comentario.

---

## § DOCKER — Contenedorización

### `docker/Dockerfile`

```dockerfile
# Multi-stage build
# Stage 1: builder con dependencias
# Stage 2: runtime slim
# Exponer puerto 8000
# Healthcheck: curl /health
# CMD: uvicorn docbot.api.app:create_app --factory --host 0.0.0.0 --port 8000
```

### `docker/docker-compose.yml`

```yaml
# Services:
# - docbot-api (build desde Dockerfile, env_file .env, ports 8000:8000)
# - postgres (neon no aplica local, usar pgvector/pgvector:pg16, ports 5432:5432)
# Volumes para persistencia de postgres
# Network compartida
```

---

## § TESTS — Criterios de Testing

```python
"""
Tests REQUERIDOS (genera estos archivos con tests funcionales):

test_parser.py:
- test_parse_markdown_with_frontmatter: parsea doc con YAML frontmatter correcto
- test_parse_markdown_without_frontmatter: parsea doc sin frontmatter
- test_content_hash_deterministic: mismo contenido → mismo hash

test_chunker.py:
- test_chunk_by_headings: split correcto por ## y ###
- test_chunk_max_tokens: chunks grandes se subdividen
- test_chunk_min_tokens_merge: chunks pequeños se fusionan
- test_chunk_preserves_heading: cada chunk tiene su heading

test_edge_extractor.py:
- test_extract_depends_on: frontmatter.depends_on genera edges correctos
- test_extract_wikilinks: [[ServiceName]] genera edge con confidence 0.7
- test_extract_helm_signals: DATABASE_URL en env → edge uses_db

test_search.py:
- test_hybrid_search_with_filters: filtros metadata se aplican
- test_hybrid_search_returns_ranked: resultados ordenados por score

test_answerer.py:
- test_answer_includes_citations: respuesta tiene al menos 2 citas
- test_answer_no_context_returns_disclaimer: sin chunks → disclaimer

test_audit.py:
- test_missing_drp_detects_service_without_drp
- test_missing_owner_detects_missing_field
"""
```

---

## § ERROR RECOVERY — Manejo de Errores

```python
"""
Implementar estos patrones de error recovery:

1. Embedding API failure:
   - Retry con exponential backoff (3 intentos)
   - Si falla, retornar error 503 con mensaje claro
   
2. DB connection failure:
   - Health endpoint retorna status='degraded'
   - Requests retornan 503

3. LLM response malformed:
   - Si no se puede parsear la respuesta del LLM, 
     retornar los chunks raw con mensaje "no pude generar respuesta estructurada"
   - Loguear el error para debugging

4. Sync de repo fallido:
   - Si git clone falla, retornar 400 con error específico
   - Si archivo individual falla parse, skip + log warning, continuar con el resto
   - Retornar conteo parcial en SyncResult

5. Edge target not found:
   - Si un depends_on referencia un servicio que no existe en DB,
     loguear warning pero NO fallar la indexación
"""
```

---

## § OBSERVABILITY — Logs Estructurados

```python
"""
Configurar structlog con:
- JSON output en producción
- Console pretty-print en desarrollo
- Campos automáticos: timestamp, level, module
- Request ID en cada request del API (middleware)
- Eventos a loguear:
  * sync.started / sync.completed / sync.failed
  * search.executed (query, filters, result_count, duration_ms)
  * answer.generated (question, citation_count, duration_ms)
  * embedding.batch (count, duration_ms)
  * edge.created / edge.skipped (reason)
"""
```

---

## § VERIFICATION — Checklist de Aceptación

Antes de considerar el proyecto completo, verifica:

- [ ] `GET /health` responde `{"status": "ok", "db_connected": true}`
- [ ] `/answer` responde con citas en formato `[repo:path#Heading]`
- [ ] `/answer` sin contexto retorna disclaimer "no encontré evidencia"
- [ ] `/search` respeta filtros de source, repo, doc_type
- [ ] `/sync` indexa solo archivos con hash cambiado (incremental)
- [ ] `/sync` elimina docs huérfanos
- [ ] `/impact` retorna grafo de dependencias con profundidad configurable
- [ ] `/list_missing` con `rule=missing_drp` detecta servicios críticos sin DRP
- [ ] `/generate_context_pack` genera markdown con 7 secciones y citas
- [ ] CLI `docbot search` muestra tabla formateada
- [ ] CLI `docbot explain` muestra respuesta + citas
- [ ] Docker Compose levanta API + Postgres funcional
- [ ] `.gitlab-ci.yml` tiene jobs para knowledge y repos producto
- [ ] Tests pasan con `pytest`
- [ ] No hay dependencias no listadas en pyproject.toml

---

## § EXECUTION ORDER — Orden de Generación

Genera los archivos en este orden para mantener coherencia:

```
1.  pyproject.toml + .env.example + .gitignore
2.  db/migrations/*.sql (schema completo)
3.  src/docbot/config.py (settings)
4.  src/docbot/database.py (pool asyncpg)
5.  src/docbot/models.py (SQLAlchemy models alineados con schema)
6.  src/docbot/embeddings.py (cliente OpenAI)
7.  src/docbot/indexer/parser.py
8.  src/docbot/indexer/chunker.py
9.  src/docbot/indexer/helm_parser.py
10. src/docbot/indexer/edge_extractor.py
11. src/docbot/indexer/sync.py
12. src/docbot/search/hybrid.py
13. src/docbot/search/graph.py
14. src/docbot/rag/prompts.py
15. src/docbot/rag/answerer.py
16. src/docbot/audit/rules.py
17. src/docbot/context_pack/generator.py
18. src/docbot/api/schemas.py
19. src/docbot/api/routes/*.py (todos)
20. src/docbot/api/app.py
21. src/docbot/cli/main.py
22. docker/Dockerfile + docker-compose.yml
23. .gitlab-ci.yml
24. tests/**
25. docs/*.md
26. README.md
```

---

## § FEW-SHOT EXAMPLES — Inputs/Outputs Esperados

### Ejemplo: Documento Obsidian de entrada

```markdown
---
title: Ticket API
doc_type: service
owner: team-backend
criticality: high
depends_on:
  - postgres-main
  - rabbitmq
related_services:
  - notification-service
  - user-api
tags:
  - api
  - tickets
  - core
---

# Ticket API

Servicio principal para gestión de tickets de atención.

## Arquitectura

Ticket API es un servicio REST basado en FastAPI que se conecta a 
[[postgres-main]] para persistencia y publica eventos a [[rabbitmq]].

## Endpoints principales

- POST /tickets — Crear ticket
- GET /tickets/{id} — Obtener ticket
- PATCH /tickets/{id}/status — Cambiar estado

## DRP

En caso de caída:
1. Verificar healthcheck en /health
2. Revisar logs en Grafana dashboard "ticket-api"
3. Si BD no responde, escalar a DBA on-call
```

### Ejemplo: Respuesta esperada de `/answer`

**Input:**
```json
{
  "question": "¿Cómo funciona Ticket API y de qué depende?"
}
```

**Output:**
```json
{
  "answer": "Ticket API es un servicio REST basado en FastAPI para gestión de tickets de atención [knowledge:services/ticket-api.md#Arquitectura]. Depende de postgres-main para persistencia y rabbitmq para publicación de eventos [knowledge:services/ticket-api.md#Arquitectura]. Sus endpoints principales incluyen creación, consulta y cambio de estado de tickets [knowledge:services/ticket-api.md#Endpoints principales].",
  "citations": [
    {"repo": "knowledge", "path": "services/ticket-api.md", "heading": "Arquitectura"},
    {"repo": "knowledge", "path": "services/ticket-api.md", "heading": "Endpoints principales"}
  ],
  "used_chunks": [
    {"doc_id": "uuid-1", "chunk_id": "uuid-1a", "score": 0.92},
    {"doc_id": "uuid-1", "chunk_id": "uuid-1b", "score": 0.87}
  ]
}
```

### Ejemplo: Respuesta esperada de `/impact`

**Input:**
```json
{
  "service": "rabbitmq",
  "change_description": "RabbitMQ down",
  "depth": 2
}
```

**Output:**
```json
{
  "nodes": [
    {"service": "rabbitmq", "doc_type": "service", "criticality": "critical"},
    {"service": "ticket-api", "doc_type": "service", "criticality": "high"},
    {"service": "notification-service", "doc_type": "service", "criticality": "medium"}
  ],
  "edges": [
    {"from_service": "ticket-api", "to_service": "rabbitmq", "relation_type": "uses_queue", "evidence": "frontmatter.depends_on in services/ticket-api.md"},
    {"from_service": "notification-service", "to_service": "rabbitmq", "relation_type": "uses_queue", "evidence": "wikilink in services/notification-service.md#Integrations"}
  ]
}
```

### Ejemplo: Respuesta esperada de `/list_missing`

**Input:**
```json
{"rule": "missing_drp"}
```

**Output:**
```json
{
  "rule": "missing_drp",
  "items": [
    {"repo": "knowledge", "path": "services/user-api.md", "title": "User API", "rule": "missing_drp", "detail": "Service with criticality=high has no DRP section"}
  ],
  "total": 1
}
```

---

*Fin del Megaprompt. Genera el proyecto completo siguiendo cada sección en orden.*
