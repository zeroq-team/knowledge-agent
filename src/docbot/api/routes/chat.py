"""Endpoint de chat multi-turno con agente LangGraph."""

from __future__ import annotations

import re
import time
import unicodedata

import structlog
from fastapi import APIRouter, HTTPException, Request

from docbot.agent.scope import allowed_domains_var, build_scope_directive, parse_scopes
from docbot.config import get_settings

from docbot.api.schemas import (
    ChatRequest,
    ChatResponse,
    CitationItem,
    ClarificationOption,
    ClarificationPayload,
    CommandInfo,
    CommandsResponse,
    FeedbackRequest,
    FeedbackResponse,
)
from docbot import build_version
from docbot.commands import get_command, list_commands
from docbot.history import store as history_store
from docbot.prompts import store as prompt_store

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


async def _resolve_command_prompt(command_name: str) -> tuple[str, str | None]:
    """Devuelve (nombre_comando, system_prompt) para un comando, leyendo del store editable.

    Cae al `system_prompt` estático del comando si el store no lo tiene.
    """
    cmd = get_command(command_name)
    if not cmd:
        return command_name, None
    key = "cmd_" + cmd.name.replace("-", "_")
    try:
        stored = await prompt_store.get_active(key)
    except Exception:  # noqa: BLE001
        stored = None
    return cmd.name, (stored or cmd.system_prompt)


def _last_user_content(messages: list[dict[str, str]]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            return m.get("content", "")
    return ""


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, request: Request) -> ChatResponse:
    """Chat multi-turno con agente LangGraph. Soporta comandos, tools y ask_user.

    Persiste el turno (pregunta + respuesta) con su metadata para análisis, y
    devuelve `conversation_id` + `message_id` para asociar el feedback 👍/👎.
    """
    from docbot.agent.graph import AgentClarification, invoke_agent

    # Auth proxy→agente: si hay proxy_token configurado, exigirlo (evita que una
    # llamada directa pública saltee el scoping por rol).
    settings = get_settings()
    if settings.proxy_token and request.headers.get("X-ZeroQ-Proxy-Token") != settings.proxy_token:
        raise HTTPException(status_code=401, detail="Proxy token requerido/ inválido")

    command = None
    command_prompt = None

    if body.command:
        command, command_prompt = await _resolve_command_prompt(body.command)

    # Identidad del usuario interno, inyectada por el proxy de knowledge-web.
    user_email = request.headers.get("X-ZeroQ-User") or None

    # Scoping por rol: dominios permitidos (CSV) resueltos server-side por el proxy.
    scopes = parse_scopes(request.headers.get("X-ZeroQ-Scopes"))
    scope_directive = build_scope_directive(scopes)

    messages = [{"role": m.role, "content": m.content} for m in body.messages]

    t0 = time.time()
    token = allowed_domains_var.set(scopes)
    try:
        result = await invoke_agent(
            messages, command_prompt=command_prompt, scope_directive=scope_directive
        )
    finally:
        allowed_domains_var.reset(token)
    latency_ms = int((time.time() - t0) * 1000)

    agent_version = build_version()
    model = settings.rag_model
    user_content = _last_user_content(messages)

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

        conversation_id, message_id = await history_store.record_exchange(
            conversation_id=body.conversation_id,
            user_email=user_email,
            user_content=user_content,
            assistant_content=result.question,
            citations=[],
            tools_used=result.tool_calls,
            prompt_key=result.prompt_key,
            prompt_version=result.prompt_version,
            agent_version=agent_version,
            command=command,
            latency_ms=latency_ms,
            model=model,
        )

        return ChatResponse(
            type="clarification",
            reply=result.question,
            citations=[],
            command=command,
            clarification=clarification,
            agent_version=agent_version,
            conversation_id=conversation_id or None,
            message_id=message_id or None,
            prompt_key=result.prompt_key,
            prompt_version=result.prompt_version,
            model=model,
        )

    # Respuesta final con citas embebidas en el texto.
    citations = _extract_citations(result.reply)

    logger.info(
        "agent_response",
        tools_used=[tc["tool"] for tc in result.tool_calls],
        citations=len(citations),
        command=command,
    )

    conversation_id, message_id = await history_store.record_exchange(
        conversation_id=body.conversation_id,
        user_email=user_email,
        user_content=user_content,
        assistant_content=result.reply,
        citations=[c.model_dump() for c in citations],
        tools_used=result.tool_calls,
        prompt_key=result.prompt_key,
        prompt_version=result.prompt_version,
        agent_version=agent_version,
        command=command,
        latency_ms=latency_ms,
        model=model,
    )

    return ChatResponse(
        type="answer",
        reply=result.reply,
        citations=citations,
        command=command,
        clarification=None,
        agent_version=agent_version,
        conversation_id=conversation_id or None,
        message_id=message_id or None,
        prompt_key=result.prompt_key,
        prompt_version=result.prompt_version,
        model=model,
        reasoning=result.reasoning,
    )


@router.post("/feedback", response_model=FeedbackResponse)
async def feedback(body: FeedbackRequest, request: Request) -> FeedbackResponse:
    """Registra el rating 👍/👎 de una respuesta del assistant (idempotente por message_id)."""
    user_email = request.headers.get("X-ZeroQ-User") or None
    ok = await history_store.set_feedback(
        message_id=body.message_id,
        rating=body.rating,
        comment=body.comment,
        user_email=user_email,
    )
    return FeedbackResponse(ok=ok)
