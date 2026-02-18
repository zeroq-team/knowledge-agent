"""Parseo de archivos Markdown con frontmatter YAML."""

from __future__ import annotations

import hashlib
import pathlib

import frontmatter

from docbot.models import ParsedDoc


def _content_hash(raw: str) -> str:
    """SHA-256 del contenido completo del archivo."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _infer_doc_type(fm: dict, rel_path: str) -> str:
    """Determina el doc_type desde frontmatter o por convención del path."""
    if "doc_type" in fm:
        return str(fm["doc_type"])
    lower = rel_path.lower()
    if "readme" in lower:
        return "readme"
    if "service" in lower:
        return "service"
    if "policy" in lower or "politica" in lower:
        return "policy"
    if "procedure" in lower or "runbook" in lower:
        return "procedure"
    return "general"


def _infer_title(fm: dict, rel_path: str, body: str) -> str:
    """Obtiene título del frontmatter, del primer heading, o del nombre de archivo."""
    if "title" in fm and fm["title"]:
        return str(fm["title"])
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped.lstrip("# ").strip()
    return pathlib.PurePosixPath(rel_path).stem.replace("-", " ").replace("_", " ").title()


def parse_file(file_path: pathlib.Path, rel_path: str) -> ParsedDoc:
    """Parsea un archivo .md extrayendo frontmatter, body y hash.

    Args:
        file_path: Ruta absoluta al archivo.
        rel_path: Ruta relativa dentro del repo (para citas).
    """
    raw = file_path.read_text(encoding="utf-8")
    post = frontmatter.loads(raw)
    fm: dict = dict(post.metadata)
    body: str = post.content

    return ParsedDoc(
        path=rel_path,
        title=_infer_title(fm, rel_path, body),
        doc_type=_infer_doc_type(fm, rel_path),
        frontmatter=fm,
        body=body,
        content_hash=_content_hash(raw),
    )
