-- Tabla de precios por modelo para estimar el costo (USD) de cada respuesta.
-- Editable en caliente desde el panel admin (GET/PUT /admin/model-prices), sin release.
-- Idempotente: el seed usa ON CONFLICT DO NOTHING para NO pisar ediciones manuales.
--
-- Precios en USD por 1.000.000 de tokens. El costo de una entrada de token_usage es
--   input_tokens/1e6 * input_usd_per_1m + output_tokens/1e6 * output_usd_per_1m
-- (los reasoning_tokens ya están incluidos en output_tokens, no se cobran aparte).
-- Los embeddings solo consumen input; su output_usd_per_1m = 0.

CREATE TABLE IF NOT EXISTS model_prices (
    model             TEXT PRIMARY KEY,
    input_usd_per_1m  NUMERIC NOT NULL DEFAULT 0,
    output_usd_per_1m NUMERIC NOT NULL DEFAULT 0,
    updated_at        TIMESTAMPTZ DEFAULT now()
);

-- Seed con los modelos en uso. Valores de referencia (revisar/editar en el panel).
INSERT INTO model_prices (model, input_usd_per_1m, output_usd_per_1m) VALUES
    ('gpt-5.2',                 1.25, 10.00),
    ('gpt-4o-mini',            0.15,  0.60),
    ('text-embedding-3-small', 0.02,  0.00)
ON CONFLICT (model) DO NOTHING;
