"""Configuración centralizada del docbot con pydantic-settings."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Todas las variables se leen desde env vars con prefijo DOCBOT_."""

    # --- Neon Postgres ---
    database_url: str

    # --- OpenAI ---
    openai_api_key: str
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536

    # --- Chunking ---
    chunk_target_tokens: int = 750
    chunk_min_tokens: int = 200
    chunk_max_tokens: int = 900

    # --- Search ---
    search_top_k: int = 10
    similarity_threshold: float = 0.7

    # --- RAG ---
    rag_model: str = "gpt-5.2"
    rag_max_context_chunks: int = 8
    rag_temperature: float = 0.1
    # Modo razonador: si tiene valor ("low"|"medium"|"high"), se envía
    # reasoning_effort al modelo y NO se manda temperature (los razonadores de
    # OpenAI la rechazan). Vacío ("") = modo clásico con temperature.
    rag_reasoning_effort: str = "medium"
    # Modelo barato/rápido para traducir el resumen de razonamiento on-demand.
    translate_model: str = "gpt-4o-mini"

    # --- Ingesta unificada desde knowledge-web (export HTTP) ---
    kb_export_url: str | None = None
    kb_export_token: str | None = None

    # --- Auth opcional para POST /sync (si se define, se exige Bearer) ---
    sync_token: str | None = None

    # --- Auth para rutas /admin/* (prompts, conversaciones, feedback stats) ---
    # Si se define, se exige Authorization: Bearer <admin_token> en esas rutas.
    admin_token: str | None = None

    # --- Auth proxy→agente para /chat (scoping por rol) ---
    # Secreto compartido con knowledge-web. Si se define, /chat exige el header
    # X-ZeroQ-Proxy-Token; sin él responde 401 (evita bypass del scoping por rol
    # llamando al agente directo). Los dominios permitidos llegan en X-ZeroQ-Scopes.
    proxy_token: str | None = None

    # --- CORS ---
    cors_origins: str = "*"

    # --- API ---
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    model_config = {"env_prefix": "DOCBOT_", "env_file": ".env"}

    def chat_llm_kwargs(self) -> dict:
        """Kwargs para el ChatOpenAI del agente de /chat.

        En modo razonador usa la Responses API con `reasoning.summary` para poder
        exponer el resumen del razonamiento (que chat.completions no devuelve).
        En modo clásico, temperature normal.
        """
        if self.rag_reasoning_effort:
            return {
                "use_responses_api": True,
                "reasoning": {"effort": self.rag_reasoning_effort, "summary": "detailed"},
            }
        return {"temperature": self.rag_temperature}

    def sampling_kwargs(self, default_temperature: float | None = None) -> dict:
        """Params de sampling según el modo del modelo.

        Razonador (rag_reasoning_effort no vacío): {"reasoning_effort": ...}, sin
        temperature. Clásico: {"temperature": ...}. Sirve igual para el SDK de
        OpenAI (chat.completions.create) y para langchain ChatOpenAI, que aceptan
        ambos kwargs con el mismo nombre.
        """
        if self.rag_reasoning_effort:
            return {"reasoning_effort": self.rag_reasoning_effort}
        t = self.rag_temperature if default_temperature is None else default_temperature
        return {"temperature": t}


def get_settings() -> Settings:
    """Singleton perezoso para la configuración."""
    return Settings()  # type: ignore[call-arg]
