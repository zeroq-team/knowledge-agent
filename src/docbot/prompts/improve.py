"""Sugeridor de mejoras de prompt asistido por IA.

Dado un prompt activo y una conversación real deficiente (con su feedback), le
pide al LLM que reescriba el prompt con cambios MÍNIMOS y dirigidos para corregir
ese tipo de respuesta, devolviendo también una explicación de qué cambió. La
propuesta NO se publica: la revisa/edita/publica el superadmin desde el panel.
"""

from __future__ import annotations

import structlog
from openai import AsyncOpenAI

from docbot.config import Settings
from docbot.prompts import store as prompt_store
from docbot.rag.prompts import ANSWER_SYSTEM_PROMPT

logger = structlog.get_logger(__name__)

_RATIONALE_MARK = "===RATIONALE==="
_PROMPT_MARK = "===PROMPT==="
_END_MARK = "===END==="

_META_SYSTEM = """\
Eres un ingeniero de prompts senior. Te entrego (1) el SYSTEM PROMPT ACTUAL de un \
agente RAG interno (ZeroQ Docbot) y (2) una conversación real donde la respuesta del \
agente fue deficiente, junto con el feedback recibido y, opcionalmente, una instrucción \
del administrador sobre qué corregir.

Tu tarea: **reescribir el system prompt** aplicando cambios **mínimos y dirigidos** que \
corrijan ESE tipo de respuesta, **preservando** la estructura, el estilo, las reglas de \
citación y el formato existentes. No reescribas secciones que no hagan falta. No inventes \
reglas nuevas que contradigan las actuales. No agregues ejemplos largos. El objetivo es \
un cambio quirúrgico, no una reescritura total.

Devuelve EXACTAMENTE este formato, sin texto adicional fuera de los marcadores:

===RATIONALE===
(2-5 viñetas breves en español: qué cambiaste y por qué, referido al problema observado)
===PROMPT===
(el system prompt COMPLETO reescrito, listo para usarse tal cual)
===END==="""


def _format_conversation(conversation: dict) -> str:
    """Arma un transcript legible de la conversación con su feedback."""
    lines: list[str] = []
    for m in conversation.get("messages", []):
        role = "USUARIO" if m.get("role") == "user" else "DOCBOT"
        lines.append(f"[{role}]\n{m.get('content', '')}")
        if m.get("role") == "assistant":
            rating = m.get("feedback_rating")
            if rating is not None:
                mark = "👍 útil" if rating == 1 else "👎 NO útil"
                comment = m.get("feedback_comment")
                fb = f"  (feedback: {mark}{'; comentario: ' + comment if comment else ''})"
                lines.append(fb)
            tools = [t.get("tool") for t in (m.get("tools_used") or [])]
            if tools:
                lines.append(f"  (tools usadas: {', '.join(str(t) for t in tools)})")
    return "\n\n".join(lines)


def _parse_output(text: str) -> tuple[str, str]:
    """Extrae (rationale, prompt) de la salida marcada. Robusto a marcadores faltantes."""
    if _PROMPT_MARK in text:
        head, _, tail = text.partition(_PROMPT_MARK)
        rationale = head.replace(_RATIONALE_MARK, "").strip()
        prompt = tail.split(_END_MARK)[0].strip()
        if prompt:
            return rationale, prompt
    # Fallback: sin marcadores usable, tratamos todo como el prompt propuesto.
    return "", text.replace(_END_MARK, "").strip()


async def suggest_improvement(
    key: str,
    conversation: dict,
    instruction: str | None,
    settings: Settings,
) -> dict:
    """Propone una reescritura del prompt `key` a partir de la conversación + instrucción."""
    active = await prompt_store.get_active_with_version(key)
    if active is not None:
        current_content, based_on_version = active
    else:
        current_content, based_on_version = ANSWER_SYSTEM_PROMPT, None

    transcript = _format_conversation(conversation)
    parts = [
        "## SYSTEM PROMPT ACTUAL (a mejorar)\n",
        current_content,
        "\n\n## CONVERSACIÓN REAL CON RESPUESTA DEFICIENTE\n",
        transcript or "(sin mensajes)",
    ]
    if instruction and instruction.strip():
        parts.append(
            "\n\n## INSTRUCCIÓN DEL ADMINISTRADOR (prioritaria)\n" + instruction.strip()
        )
    user_message = "".join(parts)

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    response = await client.chat.completions.create(
        model=settings.rag_model,
        temperature=0.2,
        messages=[
            {"role": "system", "content": _META_SYSTEM},
            {"role": "user", "content": user_message},
        ],
    )
    raw = response.choices[0].message.content or ""
    rationale, suggested_content = _parse_output(raw)

    logger.info(
        "prompt_improvement_suggested",
        key=key,
        based_on_version=based_on_version,
        suggested_len=len(suggested_content),
        has_rationale=bool(rationale),
    )

    return {
        "suggested_content": suggested_content,
        "rationale": rationale,
        "based_on_version": based_on_version,
        "model": settings.rag_model,
    }
