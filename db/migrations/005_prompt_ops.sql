-- Docbot Ops: gestión de prompts versionados + persistencia de conversaciones y feedback.
-- Idempotente: se puede correr en cada arranque sin efecto adverso.
-- Requiere pgcrypto (gen_random_uuid), habilitado en 001_extensions.sql.

-- ---------- Prompts versionados ----------

-- Un registro por prompt lógico del agente (ej: 'answer_system', 'cmd_user_story').
CREATE TABLE IF NOT EXISTS prompts (
    key            TEXT PRIMARY KEY,
    description    TEXT,
    active_version INT,
    updated_at     TIMESTAMPTZ DEFAULT now()
);

-- Historial completo de versiones por prompt. Nunca se borra: rollback = cambiar active_version.
CREATE TABLE IF NOT EXISTS prompt_versions (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key        TEXT NOT NULL REFERENCES prompts(key) ON DELETE CASCADE,
    version    INT NOT NULL,
    content    TEXT NOT NULL,
    note       TEXT,
    author     TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (key, version)
);

CREATE INDEX IF NOT EXISTS idx_prompt_versions_key ON prompt_versions (key, version DESC);

-- ---------- Conversaciones y mensajes ----------

CREATE TABLE IF NOT EXISTS conversations (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_email TEXT,
    started_at TIMESTAMPTZ DEFAULT now(),
    last_at    TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_conversations_last_at ON conversations (last_at DESC);

CREATE TABLE IF NOT EXISTS chat_messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,          -- 'user' | 'assistant'
    content         TEXT NOT NULL,
    citations       JSONB DEFAULT '[]',
    tools_used      JSONB DEFAULT '[]',
    prompt_key      TEXT,
    prompt_version  INT,
    agent_version   TEXT,
    command         TEXT,
    latency_ms      INT,
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_conversation ON chat_messages (conversation_id, created_at);

-- ---------- Feedback (rating por respuesta del assistant) ----------

CREATE TABLE IF NOT EXISTS message_feedback (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id UUID NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
    rating     SMALLINT NOT NULL CHECK (rating IN (1, -1)),  -- 1 = 👍, -1 = 👎
    comment    TEXT,
    user_email TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (message_id)   -- un rating por mensaje; se actualiza (toggle) via ON CONFLICT
);

CREATE INDEX IF NOT EXISTS idx_message_feedback_rating ON message_feedback (rating);
