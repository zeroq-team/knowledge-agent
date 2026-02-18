"""Chunking de documentos markdown por headings con control de tokens."""

from __future__ import annotations

import re

import tiktoken

from docbot.config import Settings
from docbot.models import Chunk

_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)

_encoder: tiktoken.Encoding | None = None


def _get_encoder() -> tiktoken.Encoding:
    global _encoder
    if _encoder is None:
        _encoder = tiktoken.get_encoding("cl100k_base")
    return _encoder


def _count_tokens(text: str) -> int:
    return len(_get_encoder().encode(text))


def _split_by_paragraphs(text: str, max_tokens: int) -> list[str]:
    """Subdivide texto largo por párrafos sin exceder max_tokens."""
    paragraphs = text.split("\n\n")
    parts: list[str] = []
    current: list[str] = []
    current_tokens = 0

    for para in paragraphs:
        para_tokens = _count_tokens(para)
        if current_tokens + para_tokens > max_tokens and current:
            parts.append("\n\n".join(current))
            current = [para]
            current_tokens = para_tokens
        else:
            current.append(para)
            current_tokens += para_tokens

    if current:
        parts.append("\n\n".join(current))
    return parts


def chunk_document(body: str, settings: Settings) -> list[Chunk]:
    """Divide el body de un markdown en chunks por headings.

    Estrategia:
    1. Split por headings nivel 1-3.
    2. Si un chunk > max_tokens, subdividir por párrafos.
    3. Si un chunk < min_tokens y no es el último, fusionarlo con el siguiente.
    4. Cada chunk conserva el heading bajo el que aparece.
    """
    sections: list[tuple[str | None, str]] = []
    last_end = 0
    last_heading: str | None = None

    for match in _HEADING_RE.finditer(body):
        before = body[last_end : match.start()].strip()
        if before:
            sections.append((last_heading, before))
        last_heading = match.group(2).strip()
        last_end = match.end()

    trailing = body[last_end:].strip()
    if trailing:
        sections.append((last_heading, trailing))

    if not sections and body.strip():
        sections = [(None, body.strip())]

    raw_chunks: list[Chunk] = []
    for heading, content in sections:
        tokens = _count_tokens(content)
        if tokens > settings.chunk_max_tokens:
            for part in _split_by_paragraphs(content, settings.chunk_max_tokens):
                raw_chunks.append(
                    Chunk(
                        heading=heading,
                        content=part,
                        token_count=_count_tokens(part),
                        chunk_index=0,
                    )
                )
        else:
            raw_chunks.append(
                Chunk(heading=heading, content=content, token_count=tokens, chunk_index=0)
            )

    merged: list[Chunk] = []
    i = 0
    while i < len(raw_chunks):
        current = raw_chunks[i]
        while (
            current.token_count < settings.chunk_min_tokens
            and i + 1 < len(raw_chunks)
        ):
            i += 1
            nxt = raw_chunks[i]
            current = Chunk(
                heading=current.heading,
                content=current.content + "\n\n" + nxt.content,
                token_count=_count_tokens(current.content + "\n\n" + nxt.content),
                chunk_index=0,
            )
        merged.append(current)
        i += 1

    for idx, chunk in enumerate(merged):
        chunk.chunk_index = idx

    return merged
