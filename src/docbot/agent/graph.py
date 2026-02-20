"""Grafo del agente LangGraph con ReAct loop y tools de knowledge base."""

from __future__ import annotations

import asyncpg
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from docbot.agent.tools import ALL_TOOLS, configure_tools
from docbot.config import Settings
from docbot.rag.prompts import ANSWER_SYSTEM_PROMPT

_compiled_graph = None


def build_agent(settings: Settings, pool: asyncpg.Pool):
    """Construye y cachea el grafo del agente con tools."""
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
        prompt=ANSWER_SYSTEM_PROMPT,
    )

    return _compiled_graph


def get_agent():
    """Retorna el grafo compilado o falla si no fue inicializado."""
    if _compiled_graph is None:
        raise RuntimeError("Agente no inicializado. Llama a build_agent() primero.")
    return _compiled_graph


async def invoke_agent(
    messages: list[dict[str, str]],
    *,
    command_prompt: str | None = None,
) -> tuple[str, list[dict]]:
    """Ejecuta el agente con el historial de mensajes y retorna (reply, tool_calls).

    Args:
        messages: Historial de chat [{"role": "user"|"assistant", "content": "..."}]
        command_prompt: System prompt alternativo para comandos (ej: /user-story)

    Returns:
        Tuple de (texto de respuesta, lista de tool calls ejecutadas)
    """
    agent = get_agent()

    lc_messages: list[BaseMessage] = []

    if command_prompt:
        lc_messages.append(SystemMessage(content=command_prompt))

    for msg in messages:
        if msg["role"] == "user":
            lc_messages.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            lc_messages.append(AIMessage(content=msg["content"]))

    result = await agent.ainvoke({"messages": lc_messages})

    final_messages = result["messages"]
    reply = ""
    tool_calls_info: list[dict] = []

    for msg in reversed(final_messages):
        if isinstance(msg, AIMessage) and msg.content:
            reply = msg.content
            break

    for msg in final_messages:
        if hasattr(msg, "name") and msg.name and hasattr(msg, "content"):
            tool_calls_info.append({
                "tool": msg.name,
                "result_preview": msg.content[:200] if msg.content else "",
            })

    return reply, tool_calls_info
