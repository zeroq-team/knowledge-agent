"""Cliente async para generar embeddings con OpenAI."""

from __future__ import annotations

import asyncio

import structlog
from openai import AsyncOpenAI

from docbot.config import Settings

logger = structlog.get_logger(__name__)

_client: AsyncOpenAI | None = None


def _get_client(settings: Settings) -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


async def embed_texts(
    texts: list[str],
    settings: Settings,
    *,
    max_retries: int = 3,
    batch_size: int = 100,
) -> list[list[float]]:
    """Genera embeddings en batch con retry y backoff exponencial.

    Divide la lista en lotes de ``batch_size`` para respetar los limites
    de la API y reintentar ante errores transitorios.
    """
    client = _get_client(settings)
    all_embeddings: list[list[float]] = []

    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        last_err: Exception | None = None

        for attempt in range(1, max_retries + 1):
            try:
                resp = await client.embeddings.create(
                    input=batch,
                    model=settings.embedding_model,
                )
                all_embeddings.extend([d.embedding for d in resp.data])
                logger.debug(
                    "embeddings_batch_ok",
                    batch_start=start,
                    count=len(batch),
                    attempt=attempt,
                )
                last_err = None
                break
            except Exception as exc:
                last_err = exc
                wait = 2**attempt
                logger.warning(
                    "embeddings_retry",
                    attempt=attempt,
                    wait=wait,
                    error=str(exc),
                )
                await asyncio.sleep(wait)

        if last_err is not None:
            raise RuntimeError(
                f"Fallo al generar embeddings tras {max_retries} intentos: {last_err}"
            ) from last_err

    return all_embeddings


async def embed_text(text: str, settings: Settings) -> list[float]:
    """Genera el embedding de un solo texto."""
    results = await embed_texts([text], settings)
    return results[0]
