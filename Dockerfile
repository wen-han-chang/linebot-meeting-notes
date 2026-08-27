FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY linebot_meeting ./linebot_meeting
RUN pip install --no-cache-dir .

RUN useradd --create-home appuser \
    && mkdir -p /app/records \
    && chown -R appuser:appuser /app
USER appuser

ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn linebot_meeting.app:app --host 0.0.0.0 --port ${PORT}"]
