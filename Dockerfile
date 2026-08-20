FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=5050 \
    DENO_INSTALL=/usr/local \
    PATH="/usr/local/bin:${PATH}"

WORKDIR /app

# ffmpeg = MP3; curl/unzip/ca-certificates = instalar Deno (obrigatório p/ YouTube EJS)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    ffmpeg \
    unzip \
    && curl -fsSL https://deno.land/install.sh | sh \
    && deno --version \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-docker.txt ./
# yt-dlp[default] inclui scripts EJS; Deno resolve desafios JS do YouTube
RUN pip install --upgrade pip \
    && pip install -r requirements-docker.txt \
    && pip install -U "yt-dlp[default]>=2025.8.11"

COPY *.py ./
COPY templates/ templates/
COPY static/ static/

RUN mkdir -p /app/data /app/output

EXPOSE 5050

CMD ["gunicorn", \
    "--bind", "0.0.0.0:5050", \
    "--workers", "1", \
    "--threads", "4", \
    "--timeout", "300", \
    "--keep-alive", "30", \
    "web:app"]
