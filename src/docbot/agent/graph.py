"""Grafo del agente LangGraph con ReAct loop y tools de knowledge base."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator, Union

import asyncpg
import structlog
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

logger = structlog.get_logger(__name__)

from docbot.agent.tools import ALL_TOOLS, configure_tools
from docbot.config import Settings, get_settings
from docbot import usage as usage_tracker
from docbot.prompts import store as prompt_store
from docbot.rag.prompts import ANSWER_SYSTEM_PROMPT

# Key del prompt maestro del agente en el store versionado.
ANSWER_PROMPT_KEY = "answer_system"

_compiled_graph = None


def build_agent(settings: Settings, pool: asyncpg.Pool):
    """Construye y cachea el grafo del agente con tools.

    El system prompt NO se hornea aquí: se lee del store versionado por request
    (ver `invoke_agent`), de modo que editarlo desde el panel admin surte efecto
    en caliente sin reconstruir el grafo ni reiniciar el proceso.
    """
    global _compiled_graph

    configure_tools(pool, settings)

    llm = ChatOpenAI(
        model=settings.rag_model,
        api_key=settings.openai_api_key,
        stream_usage=True,  # incluye usage_metadata al streamear (telemetría de tokens)
        **settings.chat_llm_kwargs(),
    )

    _compiled_graph = create_react_agent(
        model=llm,
        tools=ALL_TOOLS,
    )

    return _compiled_graph


async def _resolve_system_prompt() -> tuple[str, int | None]:
    """Devuelve (contenido, versión) del system prompt activo, con fallback a la constante."""
    try:
        result = await prompt_store.get_active_with_version(ANSWER_PROMPT_KEY)
        if result is not None:
            return result
    except Exception:  # noqa: BLE001 — degradar a la constante ante cualquier fallo del store
        logger.warning("prompt_store_unavailable_fallback_constant")
    return ANSWER_SYSTEM_PROMPT, None


def get_agent():
    """Retorna el grafo compilado o falla si no fue inicializado."""
    if _compiled_graph is None:
        raise RuntimeError("Agente no inicializado. Llama a build_agent() primero.")
    return _compiled_graph


# ---------- Resultados tipados de invoke_agent ----------

@dataclass
class AgentClarification:
    """El agente quiere preguntar al usuario antes de seguir buscando."""

    question: str
    options: list[str] | None = None
    reason: str | None = None
    tool_calls: list[dict] = field(default_factory=list)
    prompt_key: str | None = None
    prompt_version: int | None = None
    token_usage: list[dict] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)


@dataclass
class AgentAnswer:
    """Respuesta final del agente (con o sin tool calls intermedias)."""

    reply: str
    tool_calls: list[dict] = field(default_factory=list)
    prompt_key: str | None = None
    prompt_version: int | None = None
    reasoning: str | None = None
    token_usage: list[dict] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)


AgentResult = Union[AgentAnswer, AgentClarification]


# ---------- Labels en español para los pasos "estilo Rovo" ----------

_TOOL_RESULT_LABELS = {
    "knowledge_search": "Resultados de la búsqueda",
    "analyze_impact": "Impacto analizado",
    "get_service_detail": "Documento leído",
    "list_services": "Servicios listados",
}


def _tool_start_step(name: str, args: dict | None) -> dict:
    """Paso 'estilo Rovo' (en español) para el inicio de una tool."""
    a = args or {}
    if name == "knowledge_search":
        return {"kind": "tool_start", "label": "Buscando en la base de conocimiento",
                "detail": str(a.get("query") or "")}
    if name == "analyze_impact":
        svc = str(a.get("service_name") or "").strip()
        return {"kind": "tool_start",
                "label": f"Analizando impacto de {svc}" if svc else "Analizando impacto",
                "detail": ""}
    if name == "get_service_detail":
        svc = str(a.get("service_name") or "").strip()
        return {"kind": "tool_start", "label": f"Leyendo {svc}" if svc else "Leyendo documento",
                "detail": ""}
    if name == "list_services":
        return {"kind": "tool_start", "label": "Listando servicios", "detail": ""}
    return {"kind": "tool_start", "label": name, "detail": ""}


def _tool_result_step(name: str, content) -> dict:
    label = _TOOL_RESULT_LABELS.get(name, name)
    detail = content[:200] if isinstance(content, str) else ""
    return {"kind": "tool_result", "label": label, "detail": detail}


def _record_message_usage(m: AIMessage) -> None:
    """Registra el usage de un AIMessage del agente en el acumulador del turno."""
    um = getattr(m, "usage_metadata", None)
    if not um:
        return
    details = um.get("output_token_details") or {}
    # Usar el nombre CONFIGURADO (settings.rag_model), no el snapshot con fecha que
    # devuelve la API (ej. "gpt-4o-mini-2024-07-18"), para que matchee con la tabla
    # de precios y con la columna `model` del mensaje.
    model_name = get_settings().rag_model
    usage_tracker.record(
        model_name,
        "chat",
        input_tokens=um.get("input_tokens", 0) or 0,
        output_tokens=um.get("output_tokens", 0) or 0,
        reasoning_tokens=details.get("reasoning", 0) or 0,
        total_tokens=um.get("total_tokens", 0) or 0,
    )


def _build_lc_messages(
    messages: list[dict[str, str]],
    system_prompt: str | None,
    command_prompt: str | None,
    scope_directive: str | None = None,
) -> list[BaseMessage]:
    """Construye la lista de mensajes para LangGraph desde el historial JSON.

    El `system_prompt` (prompt maestro activo del store) va primero; el
    `command_prompt` (ej: /user-story) y el `scope_directive` (alcance por rol)
    se suman como directivas adicionales.
    """
    lc_messages: list[BaseMessage] = []

    if system_prompt:
        lc_messages.append(SystemMessage(content=system_prompt))

    if command_prompt:
        lc_messages.append(SystemMessage(content=command_prompt))

    if scope_directive:
        lc_messages.append(SystemMessage(content=scope_directive))

    for msg in messages:
        if msg["role"] == "user":
            lc_messages.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            lc_messages.append(AIMessage(content=msg["content"]))

    return lc_messages


def _content_text(content) -> str:
    """Texto plano de un content de AIMessage (str o lista de bloques de la Responses API)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return str(content)


def _content_reasoning(content) -> str:
    """Resumen de razonamiento embebido en el content (bloques con `summary`).

    La Responses API devuelve el thinking como bloques `{summary: [{text, type:
    'summary_text'}]}`. Solo aparece en modo razonador con reasoning.summary.
    """
    parts: list[str] = []
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("summary"):
                for s in b["summary"]:
                    if isinstance(s, dict) and s.get("text"):
                        parts.append(s["text"])
    return "\n\n".join(parts)


def _extract_final_reply(messages: list[BaseMessage]) -> str:
    """Devuelve el contenido textual del último AIMessage con texto."""
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content:
            text = _content_text(msg.content)
            if text.strip():
                return text
    return ""


async def stream_agent(
    messages: list[dict[str, str]],
    *,
    command_prompt: str | None = None,
    scope_directive: str | None = None,
) -> AsyncIterator[dict]:
    """Ejecuta el agente emitiendo eventos en vivo (estilo Rovo) y un evento final.

    Usa `astream(stream_mode=["updates","messages"])`:
    - "updates": inspecciona cada nodo del ReAct loop → emite pasos `step`
      (razonamiento, inicio y resultado de tools), detecta `ask_user` para
      interrumpir con `clarification`, y captura el usage de tokens del LLM.
    - "messages": streamea el texto de la respuesta final token a token
      (`answer_delta`). El razonamiento (bloques `summary`) NO se filtra al
      texto porque `_content_text` solo extrae bloques de tipo 'text'.

    Eventos emitidos (dict con clave `type`):
      {"type":"step", "kind":..., "label":..., "detail":...}
      {"type":"answer_delta", "text":...}
      {"type":"clarification", question/options/reason/tool_calls/steps/token_usage/...}
      {"type":"final", reply/reasoning/steps/tool_calls/token_usage/...}
      {"type":"error", "detail":...}

    El acumulador de usage por turno se abre acá (contextvar), de modo que el
    usage de los embeddings del retrieval (disparados dentro de las tools) cae
    en el mismo turno.
    """
    agent = get_agent()
    system_prompt, prompt_version = await _resolve_system_prompt()
    lc_messages = _build_lc_messages(messages, system_prompt, command_prompt, scope_directive)

    tool_calls_info: list[dict] = []
    reasoning_parts: list[str] = []
    steps: list[dict] = []
    streamed_answer: list[str] = []
    all_messages: list[BaseMessage] = list(lc_messages)

    usage_token = usage_tracker.start_capture()
    try:
        async for mode, chunk in agent.astream(
            {"messages": lc_messages}, stream_mode=["updates", "messages"]
        ):
            # --- Texto final token a token ---
            if mode == "messages":
                msg_chunk = chunk[0] if isinstance(chunk, tuple) else chunk
                text = _content_text(getattr(msg_chunk, "content", ""))
                if text:
                    streamed_answer.append(text)
                    yield {"type": "answer_delta", "text": text}
                continue

            # --- mode == "updates": {nodo: {messages: [...]}} ---
            update = chunk
            for node, payload in update.items():
                new_msgs = payload.get("messages", []) if isinstance(payload, dict) else []
                all_messages.extend(new_msgs)

                for m in new_msgs:
                    if isinstance(m, AIMessage):
                        _record_message_usage(m)
                        rz = _content_reasoning(m.content)
                        if rz:
                            reasoning_parts.append(rz)
                            step = {"kind": "reasoning", "label": "Analizando", "detail": rz}
                            steps.append(step)
                            yield {"type": "step", **step}

                if node == "agent":
                    if not new_msgs:
                        continue
                    ai_msg = new_msgs[-1]
                    tool_calls = getattr(ai_msg, "tool_calls", None) or []
                    for tc in tool_calls:
                        name = tc.get("name")
                        args = tc.get("args", {}) or {}
                        if name == "ask_user":
                            yield {
                                "type": "clarification",
                                "question": str(args.get("question", "")).strip(),
                                "options": args.get("options"),
                                "reason": args.get("reason"),
                                "tool_calls": tool_calls_info,
                                "steps": steps,
                                "token_usage": usage_tracker.collect(),
                                "prompt_key": ANSWER_PROMPT_KEY,
                                "prompt_version": prompt_version,
                            }
                            return
                        step = _tool_start_step(name, args)
                        steps.append(step)
                        yield {"type": "step", **step}

                elif node == "tools":
                    for m in new_msgs:
                        name = getattr(m, "name", None)
                        if name:
                            content = getattr(m, "content", "") or ""
                            tool_calls_info.append(
                                {
                                    "tool": name,
                                    "result_preview": content[:200] if isinstance(content, str) else "",
                                }
                            )
                            step = _tool_result_step(name, content)
                            steps.append(step)
                            yield {"type": "step", **step}

        reply = _extract_final_reply(all_messages)
        # Fallback: si el modelo no streameó texto, emitir la respuesta completa
        # como un único delta para que el frontend la muestre progresivamente igual.
        if reply and not streamed_answer:
            yield {"type": "answer_delta", "text": reply}

        yield {
            "type": "final",
            "reply": reply,
            "reasoning": "\n\n".join(reasoning_parts) or None,
            "steps": steps,
            "tool_calls": tool_calls_info,
            "token_usage": usage_tracker.collect(),
            "prompt_key": ANSWER_PROMPT_KEY,
            "prompt_version": prompt_version,
        }
    except Exception as err:  # noqa: BLE001 — el caller decide cómo cerrar el stream
        logger.exception("stream_agent_failed")
        yield {"type": "error", "detail": str(err)}
    finally:
        usage_tracker.reset_capture(usage_token)


async def invoke_agent(
    messages: list[dict[str, str]],
    *,
    command_prompt: str | None = None,
    scope_directive: str | None = None,
) -> AgentResult:
    """Wrapper no-stream sobre `stream_agent`: drena el generator y devuelve el
    resultado tipado (AgentAnswer | AgentClarification). Usado por `/chat` clásico.
    """
    result: AgentResult = AgentAnswer(reply="", prompt_key=ANSWER_PROMPT_KEY)
    async for ev in stream_agent(
        messages, command_prompt=command_prompt, scope_directive=scope_directive
    ):
        t = ev.get("type")
        if t == "clarification":
            return AgentClarification(
                question=ev.get("question", ""),
                options=ev.get("options"),
                reason=ev.get("reason"),
                tool_calls=ev.get("tool_calls", []),
                prompt_key=ev.get("prompt_key"),
                prompt_version=ev.get("prompt_version"),
                token_usage=ev.get("token_usage", []),
                steps=ev.get("steps", []),
            )
        if t == "final":
            return AgentAnswer(
                reply=ev.get("reply", ""),
                tool_calls=ev.get("tool_calls", []),
                prompt_key=ev.get("prompt_key"),
                prompt_version=ev.get("prompt_version"),
                reasoning=ev.get("reasoning"),
                token_usage=ev.get("token_usage", []),
                steps=ev.get("steps", []),
            )
        if t == "error":
            # Degradar a respuesta vacía; el caller ya loguea/persiste lo que haya.
            return AgentAnswer(reply="", prompt_key=ANSWER_PROMPT_KEY)
    return result
