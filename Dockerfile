FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src/ src/

RUN pip install --no-cache-dir .

COPY db/ db/

EXPOSE ${PORT:-8000}

CMD uvicorn docbot.api.app:create_app --factory --host 0.0.0.0 --port ${PORT:-8000}
