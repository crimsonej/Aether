FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml .
COPY aether/ ./aether/
COPY config/ ./config/
RUN mkdir -p data

RUN pip install --no-cache-dir -e .

EXPOSE ${PORT:-18791}

ENV PORT=18791

CMD ["sh", "-c", "uvicorn aether.core.gateway.server:app --host 0.0.0.0 --port ${PORT:-18791}"]