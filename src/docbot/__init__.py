"""ZeroQ Docbot — Agente RAG + Knowledge Graph para documentación interna."""

import os

__version__ = "0.4.0"


def build_version() -> str:
    """Versión mostrable del agente: `__version__` + commit de deploy si está.

    Railway expone RAILWAY_GIT_COMMIT_SHA; aceptamos alias comunes. Sirve para
    saber, desde la UI, si el agente que respondió es el último desplegado.
    """
    sha = (
        os.getenv("RAILWAY_GIT_COMMIT_SHA")
        or os.getenv("GIT_COMMIT_SHA")
        or os.getenv("SOURCE_COMMIT")
        or os.getenv("GIT_COMMIT")
    )
    return f"{__version__}+{sha[:7]}" if sha else __version__
