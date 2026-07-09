-- Guarda el modelo LLM usado por respuesta del assistant (visible en el panel
-- admin y en el chat para admins). Idempotente: se corre en cada arranque.

ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS model text;
