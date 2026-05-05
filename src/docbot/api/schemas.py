"""Modelos Pydantic v2 para request/response de la API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# ---------- Filtros comunes ----------

class SearchFilters(BaseModel):
    source: str | None = None
    repo: str | None = None
    doc_type: str | None = None
    path_prefix: str | None = None


# ---------- /search ----------

class SearchRequest(BaseModel):
    query: str
    filters: SearchFilters | None = None
    top_k: int = Field(default=10, ge=1, le=50)


class SearchResultItem(BaseModel):
    doc_id: str
    chunk_id: str
    repo: str
    path: str
    heading: str | None
    score: float
    snippet: str


class SearchResponse(BaseModel):
    results: list[SearchResultItem]
    total: int


# ---------- /answer ----------

class AnswerRequest(BaseModel):
    question: str = Field(min_length=5)
    filters: SearchFilters | None = None


class CitationItem(BaseModel):
    repo: str
    path: str
    heading: str | None


class UsedChunkItem(BaseModel):
    doc_id: str
    chunk_id: str
    score: float


class AnswerResponse(BaseModel):
    answer: str
    citations: list[CitationItem]
    used_chunks: list[UsedChunkItem]


# ---------- /sync ----------

class SyncRequest(BaseModel):
    source: str = "obsidian"
    repo_url: str
    branch: str = "main"
    repo_name: str | None = None


class SyncResponse(BaseModel):
    docs_indexed: int
    docs_unchanged: int
    docs_deleted: int
    chunks_created: int
    edges_created: int
    duration_seconds: float
    errors: list[str]


# ---------- /chat ----------

class ChatMessage(BaseModel):
    role: str  # 'user' | 'assistant'
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    command: str | None = None


class ClarificationOption(BaseModel):
    """Opción discreta que el frontend renderiza como chip/botón."""

    id: str
    label: str


class ClarificationPayload(BaseModel):
    """Payload con la pregunta que el agente quiere hacerle al usuario."""

    question: str
    options: list[ClarificationOption] | None = None
    allow_free_text: bool = True
    reason: str | None = None


class ChatResponse(BaseModel):
    """Respuesta del endpoint /chat.

    Cuando `type == "clarification"`, `reply` contiene la pregunta a mostrar
    y `clarification` trae el detalle estructurado (opciones + flags) para
    que el frontend renderice un componente especial.
    """

    type: Literal["answer", "clarification"] = "answer"
    reply: str
    citations: list[CitationItem] = []
    command: str | None = None
    clarification: ClarificationPayload | None = None


# ---------- /commands ----------

class CommandInfo(BaseModel):
    name: str
    description: str


class CommandsResponse(BaseModel):
    commands: list[CommandInfo]


# ---------- /health ----------

class HealthResponse(BaseModel):
    status: str
    db_connected: bool
    version: str
