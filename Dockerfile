FROM python:3.12-slim AS base

# System deps: ffmpeg for subtitle extraction. tesseract added later for M5
# (PGS/VobSub OCR).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first so the image cache survives source changes.
COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir hatchling \
    && pip install --no-cache-dir .

COPY app/ ./app/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

ENTRYPOINT ["episode-id"]
CMD ["--help"]
