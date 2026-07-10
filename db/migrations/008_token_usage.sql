-- Auditoría del razonamiento + telemetría de consumo de tokens por respuesta.
-- Idempotente: se corre en cada arranque sin efecto adverso.
--
-- token_usage: array JSON con una entrada por (modelo, kind) del turno, p.ej.
--   [{"model":"gpt-5.2","kind":"chat","input_tokens":1234,"output_tokens":567,
--     "reasoning_tokens":200,"total_tokens":1801},
--    {"model":"text-embedding-3-small","kind":"embeddings","input_tokens":88,
--     "output_tokens":0,"reasoning_tokens":0,"total_tokens":88}]
--   Nota: reasoning_tokens es un subconjunto de output_tokens (OpenAI los factura
--   como output); es informativo y NO se suma aparte al costo.
-- reasoning: resumen de razonamiento crudo (inglés) que hoy solo vivía en la
--   respuesta viva del chat; se persiste para que quede auditable tras recargar.
-- steps: timeline ordenada del "pensando" estilo Rovo
--   [{"kind":"reasoning"|"tool_start"|"tool_result","label":"...","detail":"..."}]

ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS token_usage JSONB DEFAULT '[]';
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS reasoning   TEXT;
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS steps       JSONB DEFAULT '[]';
