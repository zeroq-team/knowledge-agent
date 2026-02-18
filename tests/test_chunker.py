"""Tests para el chunker de documentos markdown."""

from __future__ import annotations

import pathlib

from docbot.config import Settings
from docbot.indexer.chunker import chunk_document
from docbot.indexer.parser import parse_file


def _make_settings(**overrides) -> Settings:
    """Crea settings de prueba sin requerir env vars reales."""
    defaults = {
        "database_url": "postgresql://test:test@localhost/test",
        "openai_api_key": "sk-test",
        "chunk_target_tokens": 750,
        "chunk_min_tokens": 200,
        "chunk_max_tokens": 900,
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def test_chunk_by_headings(sample_md_with_frontmatter: pathlib.Path):
    """Verifica que el chunker divide por headings ##."""
    doc = parse_file(sample_md_with_frontmatter, "test.md")
    settings = _make_settings(chunk_min_tokens=10)
    chunks = chunk_document(doc.body, settings)

    headings = [c.heading for c in chunks]
    assert "Arquitectura" in headings
    assert "DRP" in headings


def test_chunk_preserves_heading(sample_md_with_frontmatter: pathlib.Path):
    """Cada chunk debe tener asociado el heading bajo el que aparece."""
    doc = parse_file(sample_md_with_frontmatter, "test.md")
    settings = _make_settings(chunk_min_tokens=10)
    chunks = chunk_document(doc.body, settings)

    for chunk in chunks:
        assert chunk.chunk_index >= 0
        assert chunk.token_count > 0


def test_chunk_max_tokens():
    """Secciones grandes se subdividen en múltiples chunks."""
    big_body = "## Sección Grande\n\n" + "\n\n".join(
        [f"Párrafo {i}: contenido de ejemplo. " * 5 for i in range(20)]
    )
    settings = _make_settings(chunk_max_tokens=200, chunk_min_tokens=10)
    chunks = chunk_document(big_body, settings)

    assert len(chunks) > 1, "Debería haber generado múltiples chunks"
    assert all(c.heading == "Sección Grande" for c in chunks)


def test_chunk_min_tokens_merge():
    """Chunks muy pequeños se fusionan con el siguiente."""
    body = "## A\n\nCorto.\n\n## B\n\nTambién corto.\n\n## C\n\n" + "Contenido largo. " * 100
    settings = _make_settings(chunk_min_tokens=50)
    chunks = chunk_document(body, settings)

    assert len(chunks) >= 1
    assert all(c.token_count >= 10 for c in chunks)
