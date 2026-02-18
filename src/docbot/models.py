"""DTOs (Data Transfer Objects) para las entidades de negocio del docbot."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ParsedDoc:
    """Resultado de parsear un archivo markdown."""

    path: str
    title: str
    doc_type: str
    frontmatter: dict
    body: str
    content_hash: str


@dataclass
class Chunk:
    """Un fragmento de texto con su heading asociado."""

    heading: str | None
    content: str
    token_count: int
    chunk_index: int


@dataclass
class Edge:
    """Relación entre dos documentos."""

    from_doc_path: str
    to_doc_title: str
    relation_type: str
    evidence: str
    confidence: float = 1.0


@dataclass
class SyncResult:
    """Resultado de una operación de sincronización."""

    docs_indexed: int = 0
    docs_unchanged: int = 0
    docs_deleted: int = 0
    chunks_created: int = 0
    edges_created: int = 0
    duration_seconds: float = 0.0
    errors: list[str] = field(default_factory=list)
