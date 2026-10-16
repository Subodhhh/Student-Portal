FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

# Build context is ./backend so the shared library can be installed: docker compose build institute-enrollment-service
COPY institute-common /opt/institute-common
RUN pip install --no-cache-dir -r /opt/institute-common/requirements.txt && pip install --no-cache-dir --no-deps /opt/institute-common

COPY institute-enrollment-service/inst_enrollment ./inst_enrollment
COPY institute-enrollment-service/alembic ./alembic
COPY institute-enrollment-service/alembic.ini .

# Unprivileged user; uploads (paper service) live on a mounted volume, never in the image.
RUN useradd --create-home --uid 10001 appuser && mkdir -p /var/institute/files && chown appuser /var/institute/files
USER appuser
ENV FILE_STORE_DIR=/var/institute/files SERVICE_NAME=institute-enrollment

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/health', timeout=3)"

CMD ["uvicorn", "inst_enrollment.main:app", "--host", "0.0.0.0", "--port", "8000"]
