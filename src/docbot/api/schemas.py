"""Modelos Pydantic v2 para request/response de la API."""

from __future__ import annotations

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


class ChatResponse(BaseModel):
    reply: str
    citations: list[CitationItem]
    command: str | None = None


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
