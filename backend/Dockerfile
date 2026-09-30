FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml ./
COPY algotrade ./algotrade
RUN pip install --upgrade pip && pip install ".[research]"
# Non-root user (docs/security.md §4)
RUN useradd --create-home --uid 10001 bot && chown -R bot /app
USER bot
EXPOSE 8000
CMD ["algotrade-api"]
