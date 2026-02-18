"""Registro de comandos del docbot. Cada comando define un system prompt y si usa RAG."""

from __future__ import annotations

from dataclasses import dataclass

from docbot.commands.user_story import SYSTEM_PROMPT as USER_STORY_PROMPT


@dataclass
class Command:
    """Definición de un comando del docbot."""

    name: str
    description: str
    system_prompt: str
    use_rag: bool = True
    greeting: str = ""


COMMANDS: dict[str, Command] = {
    "user-story": Command(
        name="user-story",
        description="Genera historias de usuario con criterios de aceptación guiados",
        system_prompt=USER_STORY_PROMPT,
        use_rag=True,
        greeting=(
            "Soy el asistente de Product Analysis de ZeroQ. "
            "Te voy a guiar paso a paso para crear una historia de usuario bien definida.\n\n"
            "Para comenzar, cuéntame: **¿Qué problema o necesidad quieres resolver?** "
            "Descríbelo con tus palabras, no necesitas ser técnico."
        ),
    ),
}


def get_command(name: str) -> Command | None:
    """Obtiene un comando por nombre (sin el / inicial)."""
    return COMMANDS.get(name.lstrip("/"))


def list_commands() -> list[dict]:
    """Lista todos los comandos disponibles."""
    return [
        {"name": f"/{c.name}", "description": c.description}
        for c in COMMANDS.values()
    ]
