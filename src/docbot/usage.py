"""Telemetría de consumo de tokens por turno de chat.

Módulo hoja (sin dependencias del proyecto) para evitar imports circulares: lo
importan tanto `embeddings.py` (usage de embeddings del retrieval) como
`agent/graph.py` (usage de chat/razonamiento del LLM del agente).

El acumulador vive en un contextvar que `stream_agent`/`invoke_agent` setea al
inicio del turno. Como los tool_calls del agente corren en el mismo contexto async
(igual que `allowed_domains_var`), el usage de los embeddings disparados por
`knowledge_search` cae en el acumulador del turno automáticamente.
"""

from __future__ import annotations

import contextvars

# Lista de entradas crudas de usage del turno actual (None fuera de un turno).
_usage_var: contextvars.ContextVar[list[dict] | None] = contextvars.ContextVar(
    "docbot_usage", default=None
)


def start_capture() -> contextvars.Token:
    """Inicia un acumulador nuevo para el turno. Devolvé el token a `reset_capture`."""
    return _usage_var.set([])


def reset_capture(token: contextvars.Token) -> None:
    _usage_var.reset(token)


def record(
    model: str | None,
    kind: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    reasoning_tokens: int = 0,
    total_tokens: int | None = None,
) -> None:
    """Registra una medición de usage en el acumulador del turno (no-op si no hay).

    kind: 'chat' | 'reasoning' | 'embeddings' | 'translate' | 'improve'.
    reasoning_tokens es un subconjunto de output_tokens (informativo).
    """
    acc = _usage_var.get()
    if acc is None:
        return
    acc.append(
        {
            "model": model or "unknown",
            "kind": kind,
            "input_tokens": int(input_tokens or 0),
            "output_tokens": int(output_tokens or 0),
            "reasoning_tokens": int(reasoning_tokens or 0),
            "total_tokens": int(
                total_tokens
                if total_tokens is not None
                else (input_tokens or 0) + (output_tokens or 0)
            ),
        }
    )


def collect() -> list[dict]:
    """Devuelve el usage del turno agregado por (model, kind)."""
    acc = _usage_var.get() or []
    agg: dict[tuple[str, str], dict] = {}
    for e in acc:
        key = (e["model"], e["kind"])
        if key not in agg:
            agg[key] = {
                "model": e["model"],
                "kind": e["kind"],
                "input_tokens": 0,
                "output_tokens": 0,
                "reasoning_tokens": 0,
                "total_tokens": 0,
            }
        agg[key]["input_tokens"] += e["input_tokens"]
        agg[key]["output_tokens"] += e["output_tokens"]
        agg[key]["reasoning_tokens"] += e["reasoning_tokens"]
        agg[key]["total_tokens"] += e["total_tokens"]
    # Orden estable: primero chat/reasoning, luego embeddings, luego el resto.
    order = {"chat": 0, "reasoning": 1, "embeddings": 2}
    return sorted(agg.values(), key=lambda d: (order.get(d["kind"], 9), d["model"]))
