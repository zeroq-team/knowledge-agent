"""Scope de dominios por request para el retrieval del agente.

El proxy de knowledge-web resuelve, según el rol del usuario, qué dominios de
conocimiento puede consultar y los envía en el header `X-ZeroQ-Scopes`. La ruta
`/chat` los setea en este ContextVar antes de invocar al agente; la tool de
búsqueda los lee y los pasa a `hybrid_search` como filtro. Es seguro con 1 worker
uvicorn async (el ContextVar se propaga al task del grafo).
"""

from __future__ import annotations

import contextvars

# None = sin restricción (comportamiento legacy: ve todo).
allowed_domains_var: contextvars.ContextVar[list[str] | None] = contextvars.ContextVar(
    "allowed_domains", default=None
)

DOMAIN_LABELS: dict[str, str] = {
    "product": "Producto",
    "operations": "Operación / Incidentes",
    "technical": "Técnico / Arquitectura",
    "security": "Seguridad",
    "general": "General",
}
ALL_DOMAINS: list[str] = list(DOMAIN_LABELS.keys())

# Dominios "técnicos" (para gatear la tool analyze_impact).
TECHNICAL_DOMAINS = {"technical", "operations"}


def parse_scopes(raw: str | None) -> list[str] | None:
    """Convierte el header CSV `X-ZeroQ-Scopes` en lista de dominios válidos.

    Retorna None si no vino el header (sin restricción). Lista vacía si vino pero
    sin dominios válidos (el usuario no puede consultar nada).
    """
    if raw is None:
        return None
    parts = [p.strip() for p in raw.split(",")]
    return [p for p in parts if p in DOMAIN_LABELS]


def build_scope_directive(domains: list[str] | None) -> str | None:
    """Directiva de system prompt que explica el alcance y pide rechazo explícito.

    None si no hay restricción efectiva (sin scopes o con todos los dominios).
    """
    if domains is None:
        return None
    if set(domains) >= set(ALL_DOMAINS):
        return None
    if not domains:
        labels = "ninguno"
    else:
        labels = ", ".join(DOMAIN_LABELS.get(d, d) for d in domains)
    return (
        "ALCANCE DEL USUARIO (obligatorio): solo podés usar el contexto recuperado por las "
        f"tools, que ya viene filtrado a los dominios permitidos para este usuario: {labels}. "
        "Si la pregunta es claramente de otro dominio (por ejemplo, infraestructura, incidentes "
        "o arquitectura técnica cuando el usuario es de Producto), NO inventes ni uses "
        "conocimiento general: respondé breve y amablemente que esa información no está "
        "disponible para su perfil y que contacte a un administrador. Nunca menciones documentos "
        "que no aparezcan en el contexto recuperado."
    )
