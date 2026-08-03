FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PATH_TO_EXTENSION=/app/scraper_assistant \
    MAIN_PORT=8800

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ca-certificates curl git wget tini \
        libnss3 libnspr4 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 \
        libcups2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
        libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
        libatspi2.0-0 fonts-liberation libfreetype6 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip \
 && pip install -r /app/requirements.txt \
 && python -m playwright install chromium

COPY frontend /app/frontend
COPY app /app/app
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY docker/scraper_assistant.tar.gz /tmp/scraper_assistant.tar.gz
RUN chmod +x /usr/local/bin/entrypoint.sh

RUN mkdir -p /app/scraper_assistant \
 && tar -xzf /tmp/scraper_assistant.tar.gz -C /app/scraper_assistant --strip-components=1 \
 && rm -f /tmp/scraper_assistant.tar.gz \
 && test -f /app/scraper_assistant/manifest.json \
 && echo "scraper_assistant installed"

RUN mkdir -p /app/data /app/logs

EXPOSE 8800

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
    CMD wget -qO- http://127.0.0.1:${MAIN_PORT}/health || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["serve"]