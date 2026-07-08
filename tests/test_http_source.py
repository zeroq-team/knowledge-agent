"""Tests del parseo de ExportDoc (knowledge-web) → ParsedDoc → chunks."""

from __future__ import annotations

import hashlib

from docbot.config import Settings
from docbot.indexer.chunker import chunk_document
from docbot.indexer.parser import parse_export_doc


def _make_settings(**overrides) -> Settings:
    defaults = {
        "database_url": "postgresql://test:test@localhost/test",
        "openai_api_key": "sk-test",
        "chunk_min_tokens": 10,
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def _export_doc() -> dict:
    body = (
        "# Fila Virtual\n\n"
        "Feature que permite tomar turno remoto desde el celular.\n\n"
        "## Dolor que resuelve\n\n"
        "Evita filas físicas en sucursal.\n\n"
        "## Cómo funciona\n\n"
        "El cliente escanea un QR y recibe su turno en el teléfono."
    )
    return {
        "source": "knowledge-web",
        "repo": "products",
        "path": "products/feature/f-fila-virtual",
        "url": "/products?open=feature:f-fila-virtual",
        "title": "Fila Virtual",
        "doc_type": "feature",
        "frontmatter": {"tags": ["turnos", "mobile"]},
        "body": body,
        "content_hash": hashlib.sha256(body.encode("utf-8")).hexdigest(),
    }


def test_parse_export_doc_respects_explicit_type_and_url():
    doc = parse_export_doc(_export_doc())

    assert doc.path == "products/feature/f-fila-virtual"
    assert doc.doc_type == "feature"  # explícito, no re-inferido
    assert doc.title == "Fila Virtual"
    # el destino navegable real se preserva en el frontmatter (no hay columna url)
    assert doc.frontmatter["url"] == "/products?open=feature:f-fila-virtual"
    assert doc.frontmatter["tags"] == ["turnos", "mobile"]
    assert len(doc.content_hash) == 64


def test_parse_export_doc_chunks():
    doc = parse_export_doc(_export_doc())
    chunks = chunk_document(doc.body, _make_settings())

    assert len(chunks) >= 1
    headings = [c.heading for c in chunks]
    assert any(h in ("Fila Virtual", "Dolor que resuelve", "Cómo funciona") for h in headings)


def test_parse_export_doc_falls_back_when_type_missing():
    raw = _export_doc()
    raw["doc_type"] = ""
    raw["path"] = "products/subproduct/sp-x"
    doc = parse_export_doc(raw)
    # sin doc_type explícito, cae al inferidor por path
    assert doc.doc_type == "subproduct"


def test_parse_export_doc_recomputes_hash_when_missing():
    raw = _export_doc()
    raw.pop("content_hash")
    doc = parse_export_doc(raw)
    assert doc.content_hash == hashlib.sha256(raw["body"].encode("utf-8")).hexdigest()
