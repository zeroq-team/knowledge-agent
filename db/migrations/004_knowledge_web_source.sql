-- Habilita source='knowledge-web' para la ingesta unificada vía export HTTP.
-- El CHECK original (002_tables.sql) solo permitía 'obsidian' | 'gitlab'.
-- Idempotente: se puede correr múltiples veces sin efecto adverso.

ALTER TABLE docs DROP CONSTRAINT IF EXISTS docs_source_check;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'docs_source_check_v2'
    ) THEN
        ALTER TABLE docs
            ADD CONSTRAINT docs_source_check_v2
            CHECK (source IN ('obsidian', 'gitlab', 'knowledge-web'));
    END IF;
END
$$;
