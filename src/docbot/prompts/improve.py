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

_NOTE_MARK = "===NOTE==="
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

===NOTE===
(nota de una sola línea estilo "commit", en español, imperativa y concreta, máx. 72 \
caracteres, que resuma el cambio para el historial de versiones. Ej: "evitar KEDB/triage \
en preguntas de producto")
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


def _first_line(text: str, limit: int = 100) -> str:
    """Primera línea no vacía, recortada — fallback para la nota."""
    for ln in text.splitlines():
        ln = ln.strip().lstrip("-•* ").strip()
        if ln:
            return ln[:limit]
    return ""


def _parse_output(text: str) -> tuple[str, str, str]:
    """Extrae (note, rationale, prompt) de la salida marcada. Robusto a marcadores faltantes."""
    if _PROMPT_MARK in text:
        head, _, tail = text.partition(_PROMPT_MARK)
        prompt = tail.split(_END_MARK)[0].strip()
        # head = [===NOTE=== nota] [===RATIONALE=== rationale]
        note = ""
        rationale = head
        if _RATIONALE_MARK in head:
            note_part, _, rationale = head.partition(_RATIONALE_MARK)
            note = note_part.replace(_NOTE_MARK, "").strip()
        rationale = rationale.replace(_RATIONALE_MARK, "").replace(_NOTE_MARK, "").strip()
        if not note:
            note = _first_line(rationale)
        if prompt:
            return note, rationale, prompt
    # Fallback: sin marcadores usable, tratamos todo como el prompt propuesto.
    return "", "", text.replace(_END_MARK, "").strip()


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
        messages=[
            {"role": "system", "content": _META_SYSTEM},
            {"role": "user", "content": user_message},
        ],
        **settings.sampling_kwargs(default_temperature=0.2),
    )
    raw = response.choices[0].message.content or ""
    note, rationale, suggested_content = _parse_output(raw)

    logger.info(
        "prompt_improvement_suggested",
        key=key,
        based_on_version=based_on_version,
        suggested_len=len(suggested_content),
        has_rationale=bool(rationale),
        has_note=bool(note),
    )

    return {
        "suggested_content": suggested_content,
        "rationale": rationale,
        "note": note,
        "based_on_version": based_on_version,
        "model": settings.rag_model,
    }
