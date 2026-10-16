FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

# Build context is ./backend so the shared library can be installed: docker compose build institute-paper-service
COPY institute-common /opt/institute-common
RUN pip install --no-cache-dir -r /opt/institute-common/requirements.txt && pip install --no-cache-dir --no-deps /opt/institute-common
# Printed-text OCR for scanned papers (used only when OCR_ENGINE=tesseract).
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr && rm -rf /var/lib/apt/lists/*
COPY institute-paper-service/requirements-ocr.txt /tmp/requirements-ocr.txt
RUN pip install --no-cache-dir -r /tmp/requirements-ocr.txt

COPY institute-paper-service/inst_paper ./inst_paper
COPY institute-paper-service/alembic ./alembic
COPY institute-paper-service/alembic.ini .

# Unprivileged user; uploads (paper service) live on a mounted volume, never in the image.
RUN useradd --create-home --uid 10001 appuser && mkdir -p /var/institute/files && chown appuser /var/institute/files
USER appuser
ENV FILE_STORE_DIR=/var/institute/files SERVICE_NAME=institute-paper

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/health', timeout=3)"

CMD ["uvicorn", "inst_paper.main:app", "--host", "0.0.0.0", "--port", "8000"]
