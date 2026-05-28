FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        ca-certificates \
        fonts-liberation \
        libasound2 \
        libatk-bridge2.0-0 \
        libatk1.0-0 \
        libcairo2 \
        libcups2 \
        libdbus-1-3 \
        libdrm2 \
        libgbm1 \
        libglib2.0-0 \
        libgtk-3-0 \
        libnspr4 \
        libnss3 \
        libpango-1.0-0 \
        libx11-6 \
        libxcb1 \
        libxcomposite1 \
        libxdamage1 \
        libxext6 \
        libxfixes3 \
        libxkbcommon0 \
        libxrandr2 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install chromium

COPY . .

RUN mkdir -p /data/uploads

ENV FLASK_ENV=production \
    HOST=0.0.0.0 \
    PORT=5455 \
    DATABASE_URL=sqlite:////data/analytics.db \
    UPLOAD_DIR=/data/uploads \
    AUTH_REQUIRED=true \
    TRANSCRIPTION_ENABLED=false \
    ENABLE_OPENCLAW=false

EXPOSE 5455

CMD ["sh", "-c", "gunicorn app:app --bind 0.0.0.0:${PORT:-5455} --workers ${WEB_CONCURRENCY:-2} --timeout ${GUNICORN_TIMEOUT:-180}"]
