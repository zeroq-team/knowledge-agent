"""Endpoint de chat multi-turno con agente LangGraph."""

from __future__ import annotations

import re

import structlog
from fastapi import APIRouter, Request

from docbot.api.schemas import (
    ChatRequest,
    ChatResponse,
    CitationItem,
    CommandInfo,
    CommandsResponse,
)
from docbot.commands import get_command, list_commands

router = APIRouter()
logger = structlog.get_logger(__name__)

_CITATION_RE = re.compile(r"\[(?:\d+\]\s*)?([^\]\[:]+):([^\]#]+)#([^\]]+)\]")


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
    """Chat multi-turno con agente LangGraph. Soporta comandos y tools."""
    from docbot.agent.graph import invoke_agent

    command = None
    command_prompt = None

    if body.command:
        cmd = get_command(body.command)
        if cmd:
            command = cmd.name
            command_prompt = cmd.system_prompt

    messages = [{"role": m.role, "content": m.content} for m in body.messages]

    reply, tool_calls = await invoke_agent(
        messages, command_prompt=command_prompt,
    )

    citations = _extract_citations(reply)

    logger.info(
        "agent_response",
        tools_used=[tc["tool"] for tc in tool_calls],
        citations=len(citations),
        command=command,
    )

    return ChatResponse(reply=reply, citations=citations, command=command)
