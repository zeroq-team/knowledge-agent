"""Fixtures compartidas para tests."""

from __future__ import annotations

import pathlib
import textwrap

import pytest


@pytest.fixture
def sample_md_with_frontmatter(tmp_path: pathlib.Path) -> pathlib.Path:
    """Crea un archivo .md de ejemplo con frontmatter YAML."""
    content = textwrap.dedent("""\
        ---
        title: Ticket API
        doc_type: service
        owner: team-backend
        criticality: high
        depends_on:
          - postgres-main
          - rabbitmq
        related_services:
          - notification-service
        tags:
          - api
          - tickets
        ---

        # Ticket API

        Servicio principal para gestión de tickets de atención.

        ## Arquitectura

        Ticket API es un servicio REST basado en FastAPI que se conecta a
        [[postgres-main]] para persistencia y publica eventos a [[rabbitmq]].

        ## Endpoints principales

        - POST /tickets — Crear ticket
        - GET /tickets/{id} — Obtener ticket
        - PATCH /tickets/{id}/status — Cambiar estado

        ## DRP

        En caso de caída:
        1. Verificar healthcheck en /health
        2. Revisar logs en Grafana dashboard "ticket-api"
        3. Si BD no responde, escalar a DBA on-call
    """)
    f = tmp_path / "ticket-api.md"
    f.write_text(content, encoding="utf-8")
    return f


@pytest.fixture
def sample_md_without_frontmatter(tmp_path: pathlib.Path) -> pathlib.Path:
    """Crea un archivo .md sin frontmatter."""
    content = textwrap.dedent("""\
        # Guía de onboarding

        Bienvenido al equipo de ZeroQ.

        ## Paso 1

        Configurar tu entorno de desarrollo.

        ## Paso 2

        Revisar la documentación interna.
    """)
    f = tmp_path / "onboarding.md"
    f.write_text(content, encoding="utf-8")
    return f
