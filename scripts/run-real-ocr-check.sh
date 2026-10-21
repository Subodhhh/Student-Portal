#!/usr/bin/env bash
# Real-Tesseract checks of photo papers, inside the paper-service image (the only place the OCR engine is installed). Uses this
# working tree's code (mounted read-only) and pytest installed into the throwaway container; nothing persistent is touched.
#   scripts/run-real-ocr-check.sh [image]      (default image: institute-paper-service:latest)
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
docker run --rm -v "$ROOT/institute-backend:/src:ro" -w /src -e DATABASE_URL=sqlite:// -e PYTHONDONTWRITEBYTECODE=1 \
  -e PYTHONPATH=/src/institute-paper-service:/src/institute-common "${1:-institute-paper-service:latest}" \
  sh -c 'pip install -q --disable-pip-version-check pytest==8.3.3 >/dev/null 2>&1 && tesseract --version | head -1 && python -m pytest -q -p no:cacheprovider institute-paper-service/ocr_checks'
