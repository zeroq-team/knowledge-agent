CREATE TABLE IF NOT EXISTS docs (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source       TEXT NOT NULL CHECK (source IN ('obsidian', 'gitlab')),
    repo         TEXT NOT NULL,
    path         TEXT NOT NULL,
    title        TEXT NOT NULL,
    doc_type     TEXT NOT NULL,
    frontmatter  JSONB DEFAULT '{}',
    content_hash TEXT NOT NULL,
    updated_at   TIMESTAMPTZ DEFAULT now(),
    UNIQUE (repo, path, source)
);

CREATE TABLE IF NOT EXISTS doc_chunks (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    doc_id      UUID NOT NULL REFERENCES docs(id) ON DELETE CASCADE,
    chunk_index INT NOT NULL,
    heading     TEXT,
    content     TEXT NOT NULL,
    token_count INT NOT NULL,
    embedding   vector(1536),
    UNIQUE (doc_id, chunk_index)
);

CREATE TABLE IF NOT EXISTS edges (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    from_doc_id   UUID NOT NULL REFERENCES docs(id) ON DELETE CASCADE,
    to_doc_id     UUID NOT NULL REFERENCES docs(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL,
    evidence      TEXT,
    confidence    REAL DEFAULT 1.0,
    updated_at    TIMESTAMPTZ DEFAULT now()
);
