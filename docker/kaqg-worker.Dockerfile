FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    KAQG_REPO=/opt/upstream/kaqg

COPY upstream/kaqg /opt/upstream/kaqg
COPY kaqg_worker /app/kaqg_worker

RUN pip install --no-cache-dir \
    fastapi==0.116.1 \
    uvicorn==0.35.0 \
    neo4j \
    openai \
    PyMuPDF

EXPOSE 18100

CMD ["uvicorn", "kaqg_worker.app:app", "--host", "0.0.0.0", "--port", "18100"]
