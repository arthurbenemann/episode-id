FROM python:3.12-slim AS base

# System deps: ffmpeg for subtitle extraction. tesseract added later for M5
# (PGS/VobSub OCR).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# hatchling's `packages = ["app"]` resolves at install time, so the source
# tree has to exist before `pip install .`. We trade weaker layer caching
# for a build that actually works.
COPY pyproject.toml README.md ./
COPY app/ ./app/
RUN pip install --no-cache-dir hatchling \
    && pip install --no-cache-dir .

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8080

# Default to serving the web app. The CLI is still reachable via
# `docker run --entrypoint episode-id ...`.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
