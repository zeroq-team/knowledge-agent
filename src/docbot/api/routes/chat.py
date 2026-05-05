"""Endpoint de chat multi-turno con agente LangGraph."""

from __future__ import annotations

import re
import unicodedata

import structlog
from fastapi import APIRouter, Request

from docbot.api.schemas import (
    ChatRequest,
    ChatResponse,
    CitationItem,
    ClarificationOption,
    ClarificationPayload,
    CommandInfo,
    CommandsResponse,
)
from docbot.commands import get_command, list_commands

router = APIRouter()
logger = structlog.get_logger(__name__)

_CITATION_RE = re.compile(r"\[(?:\d+\]\s*)?([^\]\[:]+):([^\]#]+)#([^\]]+)\]")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _extract_citations(text: str) -> list[CitationItem]:
    seen: set[str] = set()
    results: list[CitationItem] = []
    for m in _CITATION_RE.finditer(text):
        key = f"{m.group(1)}:{m.group(2)}#{m.group(3)}"
        if key not in seen:
            seen.add(key)
            results.append(CitationItem(repo=m.group(1), path=m.group(2), heading=m.group(3)))
    return results


def _slugify(label: str) -> str:
    """Genera un id estable a partir de un label legible.

    Quita acentos y caracteres no alfanuméricos. Ej: "Cartelería Digital" -> "carteleria-digital".
    """
    normalized = unicodedata.normalize("NFKD", label)
    ascii_only = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    slug = _SLUG_RE.sub("-", ascii_only.lower()).strip("-")
    return slug or "option"


@router.get("/commands", response_model=CommandsResponse)
async def commands_list() -> CommandsResponse:
    """Lista los comandos disponibles."""
    return CommandsResponse(
        commands=[CommandInfo(**c) for c in list_commands()]
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, request: Request) -> ChatResponse:
    """Chat multi-turno con agente LangGraph. Soporta comandos, tools y ask_user."""
    from docbot.agent.graph import AgentClarification, invoke_agent

    command = None
    command_prompt = None

    if body.command:
        cmd = get_command(body.command)
        if cmd:
            command = cmd.name
            command_prompt = cmd.system_prompt

    messages = [{"role": m.role, "content": m.content} for m in body.messages]

    result = await invoke_agent(messages, command_prompt=command_prompt)

    # El agente decidió pedir clarificación al usuario antes de buscar.
    if isinstance(result, AgentClarification):
        options: list[ClarificationOption] | None = None
        if result.options:
            options = [
                ClarificationOption(id=_slugify(opt), label=opt) for opt in result.options
            ]

        clarification = ClarificationPayload(
            question=result.question,
            options=options,
            allow_free_text=True,
            reason=result.reason,
        )

        logger.info(
            "agent_clarification",
            tools_used=[tc["tool"] for tc in result.tool_calls],
            options=len(options) if options else 0,
            command=command,
            reason=result.reason,
        )

        return ChatResponse(
            type="clarification",
            reply=result.question,
            citations=[],
            command=command,
            clarification=clarification,
        )

    # Respuesta final con citas embebidas en el texto.
    citations = _extract_citations(result.reply)

    logger.info(
        "agent_response",
        tools_used=[tc["tool"] for tc in result.tool_calls],
        citations=len(citations),
        command=command,
    )

    return ChatResponse(
        type="answer",
        reply=result.reply,
        citations=citations,
        command=command,
        clarification=None,
    )
