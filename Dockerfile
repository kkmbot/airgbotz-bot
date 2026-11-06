# Multi-arch: all dependencies are pure Python or ship wheels for amd64 and arm64.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DB_PATH=/data/bot.sqlite3

WORKDIR /app

RUN useradd --create-home --uid 10001 botuser \
    && mkdir -p /data \
    && chown botuser:botuser /data

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ai_bot ./ai_bot

USER botuser
VOLUME ["/data"]

# Long polling: no port to expose, no webhook to configure.
CMD ["python", "-m", "ai_bot"]
