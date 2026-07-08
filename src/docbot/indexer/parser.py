"""Parseo de archivos Markdown con frontmatter YAML."""

from __future__ import annotations

import hashlib
import pathlib

import frontmatter
import structlog

from docbot.models import ParsedDoc

logger = structlog.get_logger(__name__)

# Tipos de documento que el export de knowledge-web declara EXPLÍCITAMENTE
# (además de los tipos clásicos del vault). No se re-infieren.
KNOWLEDGE_WEB_DOC_TYPES = frozenset(
    {"product", "subproduct", "feature", "scenario", "kedb", "triage"}
)


def _content_hash(raw: str) -> str:
    """SHA-256 del contenido completo del archivo."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _infer_doc_type(fm: dict, rel_path: str) -> str:
    """Determina el doc_type desde frontmatter o por convención de path/prefijo.

    Prioriza la clave canónica ``type`` (definida en
    ``00-Governance/Metadata-Schema.md``); ``doc_type`` se mantiene como alias
    legacy. Si el frontmatter no la trae, se infiere por carpeta o prefijo del
    nombre de archivo, cubriendo todos los dominios del vault.
    """
    for key in ("type", "doc_type"):
        value = fm.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()

    lower = rel_path.lower()
    fname = lower.rsplit("/", 1)[-1]

    rules: list[tuple[str, str]] = [
        ("/runbooks/", "runbook"),
        ("/06-rfp-knowledge/", "rfp"),
        ("/integrations/", "integration"),
        ("/infrastructure/", "infra"),
        ("/services/", "service"),
        ("/policies/", "policy"),
        ("/modules/", "module"),
        ("/features/", "feature"),
        ("/templates/", "template"),
        ("/data-flow/", "data_flow"),
        # Dominios de knowledge-web (fallback; normalmente vienen con doc_type explícito).
        ("products/subproduct", "subproduct"),
        ("products/product", "product"),
        ("products/feature", "feature"),
        ("playbook/", "scenario"),
        ("autoservicio/kedb", "kedb"),
        ("autoservicio/triage", "triage"),
    ]
    for needle, dtype in rules:
        if needle in lower:
            return dtype

    prefix_rules: list[tuple[str, str]] = [
        ("rb-", "runbook"),
        ("svc-", "service"),
        ("infra-", "infra"),
        ("int-", "integration"),
        ("pol-", "policy"),
        ("proc-", "procedure"),
        ("playbook-", "playbook"),
        ("drp-", "drp"),
        ("rfp-", "rfp"),
        ("mod-", "module"),
        ("feat-", "feature"),
        ("agent-", "agent"),
        ("skill-", "skill"),
        ("template-", "template"),
        ("df-", "data_flow"),
        ("std-", "standard"),
        ("plat-", "platform"),
        ("carteleria-", "client_module"),
    ]
    for needle, dtype in prefix_rules:
        if fname.startswith(needle):
            return dtype

    if "readme" in fname:
        return "readme"
    return "general"


def _infer_title(fm: dict, rel_path: str, body: str) -> str:
    """Obtiene título del frontmatter, del primer heading, o del nombre de archivo."""
    if "title" in fm and fm["title"]:
        return str(fm["title"])
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped.lstrip("# ").strip()
    return pathlib.PurePosixPath(rel_path).stem.replace("-", " ").replace("_", " ").title()


def _strip_frontmatter_block(raw: str) -> str:
    """Quita el bloque ``---\\n...\\n---`` inicial si existe, y devuelve el body.

    Se usa como fallback cuando el YAML del frontmatter no es parseable
    (ej. templates Obsidian con placeholders ``{{...}}``). Si no hay
    delimitadores válidos, retorna ``raw`` sin cambios.
    """
    if not raw.startswith("---"):
        return raw
    rest = raw[3:]
    end = rest.find("\n---")
    if end == -1:
        return raw
    body_start = end + len("\n---")
    if body_start < len(rest) and rest[body_start] == "\n":
        body_start += 1
    return rest[body_start:]


def parse_file(file_path: pathlib.Path, rel_path: str) -> ParsedDoc:
    """Parsea un archivo .md extrayendo frontmatter, body y hash.

    Si el YAML del frontmatter es inválido (ej. templates con
    ``{{date:YYYY-MM-DD}}`` de Obsidian Templater), el archivo se indexa
    igual con ``frontmatter={}`` y el ``doc_type`` se infiere por path.

    Args:
        file_path: Ruta absoluta al archivo.
        rel_path: Ruta relativa dentro del repo (para citas).
    """
    raw = file_path.read_text(encoding="utf-8")
    try:
        post = frontmatter.loads(raw)
        fm: dict = dict(post.metadata)
        body: str = post.content
    except Exception as exc:
        logger.warning(
            "frontmatter_parse_failed",
            path=rel_path,
            error=str(exc).split("\n", 1)[0],
        )
        fm = {}
        body = _strip_frontmatter_block(raw)

    return ParsedDoc(
        path=rel_path,
        title=_infer_title(fm, rel_path, body),
        doc_type=_infer_doc_type(fm, rel_path),
        frontmatter=fm,
        body=body,
        content_hash=_content_hash(raw),
    )


def parse_export_doc(doc: dict) -> ParsedDoc:
    """Convierte un ``ExportDoc`` del endpoint de knowledge-web en un ``ParsedDoc``.

    A diferencia de :func:`parse_file`:

    - El ``doc_type`` viene EXPLÍCITO en el payload y **no** se re-infiere
      (solo se cae a ``_infer_doc_type`` si el export no lo trae).
    - El ``content_hash`` lo provee el export (sha256 del ``body``); se usa tal
      cual para la incrementalidad. Si faltara, se recomputa localmente.
    - El destino navegable real (``url``) se guarda dentro del ``frontmatter``
      JSONB (la tabla ``docs`` no tiene columna dedicada), quedando disponible
      en la metadata del doc.

    Campos esperados en ``doc``: ``path``, ``title``, ``doc_type``,
    ``frontmatter``, ``body``, ``content_hash``, ``url``.
    """
    path = str(doc.get("path") or "").strip()
    if not path:
        raise ValueError("ExportDoc sin 'path'")

    body = doc.get("body") or ""
    fm: dict = dict(doc.get("frontmatter") or {})

    url = doc.get("url")
    if url and "url" not in fm:
        fm["url"] = url

    explicit_type = (doc.get("doc_type") or "").strip().lower()
    doc_type = explicit_type or _infer_doc_type(fm, path)

    title = (doc.get("title") or "").strip() or _infer_title(fm, path, body)

    content_hash = (doc.get("content_hash") or "").strip() or _content_hash(body)

    return ParsedDoc(
        path=path,
        title=title,
        doc_type=doc_type,
        frontmatter=fm,
        body=body,
        content_hash=content_hash,
    )
