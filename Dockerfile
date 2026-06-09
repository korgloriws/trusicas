FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=5050

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-docker.txt .
RUN pip install --upgrade pip && pip install -r requirements-docker.txt

COPY auth.py client.py config.py generate.py json_extract.py ping_model.py prompt.py render_md.py store.py web.py __main__.py ./
COPY templates/ templates/
COPY static/ static/

RUN mkdir -p /app/data /app/output

EXPOSE 5050

# Geração via OpenRouter pode demorar vários minutos
CMD ["gunicorn", \
    "--bind", "0.0.0.0:5050", \
    "--workers", "1", \
    "--threads", "4", \
    "--timeout", "300", \
    "--keep-alive", "30", \
    "web:app"]
