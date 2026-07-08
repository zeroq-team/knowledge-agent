-- Dominio de conocimiento por documento (para scoping del retrieval por rol).
-- Se popula en el indexado (indexer/http_source.derive_domain). Idempotente.
ALTER TABLE docs ADD COLUMN IF NOT EXISTS domain TEXT;
CREATE INDEX IF NOT EXISTS idx_docs_domain ON docs(domain);
