"""Grafo del agente LangGraph con ReAct loop y tools de knowledge base."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union

import asyncpg
import structlog
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

logger = structlog.get_logger(__name__)

from docbot.agent.tools import ALL_TOOLS, configure_tools
from docbot.config import Settings
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
        temperature=settings.rag_temperature,
        api_key=settings.openai_api_key,
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


@dataclass
class AgentAnswer:
    """Respuesta final del agente (con o sin tool calls intermedias)."""

    reply: str
    tool_calls: list[dict] = field(default_factory=list)
    prompt_key: str | None = None
    prompt_version: int | None = None


AgentResult = Union[AgentAnswer, AgentClarification]


def _build_lc_messages(
    messages: list[dict[str, str]],
    system_prompt: str | None,
    command_prompt: str | None,
) -> list[BaseMessage]:
    """Construye la lista de mensajes para LangGraph desde el historial JSON.

    El `system_prompt` (prompt maestro activo del store) va primero; el
    `command_prompt` (ej: /user-story) se suma como directiva adicional.
    """
    lc_messages: list[BaseMessage] = []

    if system_prompt:
        lc_messages.append(SystemMessage(content=system_prompt))

    if command_prompt:
        lc_messages.append(SystemMessage(content=command_prompt))

    for msg in messages:
        if msg["role"] == "user":
            lc_messages.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            lc_messages.append(AIMessage(content=msg["content"]))

    return lc_messages


def _extract_final_reply(messages: list[BaseMessage]) -> str:
    """Devuelve el contenido del último AIMessage con texto."""
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content:
            return msg.content if isinstance(msg.content, str) else str(msg.content)
    return ""


async def invoke_agent(
    messages: list[dict[str, str]],
    *,
    command_prompt: str | None = None,
) -> AgentResult:
    """Ejecuta el agente y retorna AgentAnswer o AgentClarification.

    Usa `astream(stream_mode="updates")` para inspeccionar cada paso del grafo:
    - Si en el nodo `agent` el LLM emite un tool_call con name="ask_user",
      se interrumpe el ReAct loop ANTES de ejecutar la tool y se retorna
      AgentClarification con la pregunta.
    - En cualquier otro caso se acumulan los tool_calls intermedios y se
      retorna AgentAnswer con el texto del último AIMessage.

    Args:
        messages: Historial [{"role": "user"|"assistant", "content": "..."}].
        command_prompt: System prompt alternativo para comandos (ej: /user-story).

    Returns:
        AgentAnswer o AgentClarification.
    """
    agent = get_agent()
    system_prompt, prompt_version = await _resolve_system_prompt()
    lc_messages = _build_lc_messages(messages, system_prompt, command_prompt)

    tool_calls_info: list[dict] = []
    all_messages: list[BaseMessage] = list(lc_messages)

    async for update in agent.astream(
        {"messages": lc_messages}, stream_mode="updates"
    ):
        # update es {nodo: {messages: [...nuevos mensajes...]}}
        for node, payload in update.items():
            new_msgs = payload.get("messages", []) if isinstance(payload, dict) else []
            all_messages.extend(new_msgs)

            if node == "agent":
                # Inspeccionar tool_calls del último AIMessage para detectar ask_user
                if not new_msgs:
                    continue
                ai_msg = new_msgs[-1]
                tool_calls = getattr(ai_msg, "tool_calls", None) or []
                for tc in tool_calls:
                    if tc.get("name") == "ask_user":
                        args = tc.get("args", {}) or {}
                        return AgentClarification(
                            question=str(args.get("question", "")).strip(),
                            options=args.get("options"),
                            reason=args.get("reason"),
                            tool_calls=tool_calls_info,
                            prompt_key=ANSWER_PROMPT_KEY,
                            prompt_version=prompt_version,
                        )

            elif node == "tools":
                # Loguear las tools que sí se ejecutaron
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

    reply = _extract_final_reply(all_messages)
    return AgentAnswer(
        reply=reply,
        tool_calls=tool_calls_info,
        prompt_key=ANSWER_PROMPT_KEY,
        prompt_version=prompt_version,
    )
