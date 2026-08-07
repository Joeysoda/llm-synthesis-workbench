FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH=/app/.venv/bin:$PATH

COPY pyproject.toml uv.lock ./
COPY upstream/synthetic-data-kit /opt/upstream/synthetic-data-kit
COPY upstream/synlogic /opt/upstream/synlogic
COPY upstream/kaqg /opt/upstream/kaqg

RUN pip install --no-cache-dir uv \
    && uv sync --frozen --no-dev \
    && uv pip install \
        --python /app/.venv/bin/python \
        /opt/upstream/synthetic-data-kit

COPY gateway /app/gateway

EXPOSE 18000

CMD ["uvicorn", "app.main:app", "--app-dir", "/app/gateway", "--host", "0.0.0.0", "--port", "18000"]
