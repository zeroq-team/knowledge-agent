CREATE INDEX IF NOT EXISTS idx_chunks_embedding
    ON doc_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS idx_docs_source_repo_type
    ON docs (source, repo, doc_type);

CREATE INDEX IF NOT EXISTS idx_docs_frontmatter
    ON docs USING GIN (frontmatter);

CREATE INDEX IF NOT EXISTS idx_edges_from ON edges (from_doc_id);
CREATE INDEX IF NOT EXISTS idx_edges_to ON edges (to_doc_id);
CREATE INDEX IF NOT EXISTS idx_edges_type ON edges (relation_type);
