"""Tests para el parser de markdown + frontmatter."""

from __future__ import annotations

import pathlib

from docbot.indexer.parser import parse_file


def test_parse_with_frontmatter(sample_md_with_frontmatter: pathlib.Path):
    """Verifica parseo correcto de un .md con frontmatter YAML."""
    doc = parse_file(sample_md_with_frontmatter, "services/ticket-api.md")

    assert doc.title == "Ticket API"
    assert doc.doc_type == "service"
    assert doc.frontmatter["owner"] == "team-backend"
    assert doc.frontmatter["criticality"] == "high"
    assert "postgres-main" in doc.frontmatter["depends_on"]
    assert "rabbitmq" in doc.frontmatter["depends_on"]
    assert "# Ticket API" in doc.body
    assert len(doc.content_hash) == 64


def test_parse_without_frontmatter(sample_md_without_frontmatter: pathlib.Path):
    """Verifica parseo de un .md sin frontmatter."""
    doc = parse_file(sample_md_without_frontmatter, "guides/onboarding.md")

    assert doc.title == "Guía de onboarding"
    assert doc.frontmatter == {}
    assert "Bienvenido" in doc.body


def test_content_hash_deterministic(sample_md_with_frontmatter: pathlib.Path):
    """El mismo contenido siempre produce el mismo hash."""
    doc1 = parse_file(sample_md_with_frontmatter, "a.md")
    doc2 = parse_file(sample_md_with_frontmatter, "a.md")
    assert doc1.content_hash == doc2.content_hash
