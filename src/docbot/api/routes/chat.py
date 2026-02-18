"""Endpoint de chat multi-turno con soporte para comandos."""

from __future__ import annotations

import re

from fastapi import APIRouter, Request

from docbot.api.schemas import (
    ChatRequest,
    ChatResponse,
    CitationItem,
    CommandInfo,
    CommandsResponse,
)
from docbot.commands import get_command, list_commands
from docbot.config import get_settings
from docbot.embeddings import embed_text
from docbot.rag.prompts import ANSWER_SYSTEM_PROMPT
from docbot.search.hybrid import hybrid_search

router = APIRouter()

_CITATION_RE = re.compile(r"\[(?:\d+\]\s*)?([^\]\[:]+):([^\]#]+)#([^\]]+)\]")


def _format_chunks(chunks: list) -> str:
    parts: list[str] = []
    for i, c in enumerate(chunks, 1):
        heading_part = f"#{c.heading}" if c.heading else ""
        parts.append(f"[{i}] {c.repo}:{c.path}{heading_part}\n{c.snippet}")
    return "\n\n".join(parts)


def _extract_citations(text: str) -> list[CitationItem]:
    seen: set[str] = set()
    results: list[CitationItem] = []
    for m in _CITATION_RE.finditer(text):
        key = f"{m.group(1)}:{m.group(2)}#{m.group(3)}"
        if key not in seen:
            seen.add(key)
            results.append(CitationItem(repo=m.group(1), path=m.group(2), heading=m.group(3)))
    return results


@router.get("/commands", response_model=CommandsResponse)
async def commands_list() -> CommandsResponse:
    """Lista los comandos disponibles."""
    return CommandsResponse(
        commands=[CommandInfo(**c) for c in list_commands()]
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, request: Request) -> ChatResponse:
    """Chat multi-turno. Soporta comandos (ej: /user-story) y modo RAG default."""
    settings = get_settings()
    pool = request.app.state.pool
    from openai import AsyncOpenAI

    command = None
    system_prompt = ANSWER_SYSTEM_PROMPT
    use_rag = True

    if body.command:
        cmd = get_command(body.command)
        if cmd:
            command = cmd.name
            system_prompt = cmd.system_prompt
            use_rag = cmd.use_rag

    last_user_msg = ""
    for m in reversed(body.messages):
        if m.role == "user":
            last_user_msg = m.content
            break

    context_block = ""
    if use_rag and last_user_msg:
        try:
            query_embedding = await embed_text(last_user_msg, settings)
            async with pool.acquire() as conn:
                chunks = await hybrid_search(conn, query_embedding, top_k=6)
            if chunks:
                context_block = (
                    "\n\n--- CONTEXTO DE LA BASE DE CONOCIMIENTO ---\n"
                    + _format_chunks(chunks)
                    + "\n--- FIN CONTEXTO ---\n\n"
                )
        except Exception:
            pass

    full_system = system_prompt
    if context_block:
        full_system += context_block

    openai_messages = [{"role": "system", "content": full_system}]
    for m in body.messages:
        openai_messages.append({"role": m.role, "content": m.content})

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    response = await client.chat.completions.create(
        model=settings.rag_model,
        temperature=settings.rag_temperature if not command else 0.3,
        messages=openai_messages,
    )

    reply = response.choices[0].message.content or ""
    citations = _extract_citations(reply)

    return ChatResponse(reply=reply, citations=citations, command=command)
