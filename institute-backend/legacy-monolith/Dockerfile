FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .

# Run as an unprivileged user; uploads live on a mounted volume, never in the image.
RUN useradd --create-home --uid 10001 appuser && mkdir -p /var/institute/files && chown appuser /var/institute/files
USER appuser
ENV FILE_STORE_DIR=/var/institute/files

EXPOSE 8001
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8001/health', timeout=3)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
