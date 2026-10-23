#!/usr/bin/env bash
# Real-Tesseract checks of photo papers, inside the paper-service image (the only place the OCR engine is installed). Uses this
# working tree's code (mounted read-only) and pytest installed into the throwaway container; nothing persistent is touched.
#   [OCR_PRIVATE_DIR=~/ocr-private] scripts/run-real-ocr-check.sh [image]      (default image: institute-paper-service:latest)
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
# The whole repository is mounted: the photo-condition checks reuse the e2e photo simulator (tests/e2e/phone_photos.py).
# OCR_PRIVATE_DIR (optional, on the host): a folder of private real photos, mounted read-only for ocr_checks/test_private_photos.py.
PRIVATE=()
# The folder stays private (e.g. mode 700): the container then runs as the caller's user id instead of the image's user.
if [ -n "${OCR_PRIVATE_DIR:-}" ]; then
  PRIVATE=(-v "$OCR_PRIVATE_DIR:/private:ro" -e OCR_PRIVATE_DIR=/private -u "$(id -u):$(id -g)" -e HOME=/tmp)
fi
docker run --rm "${PRIVATE[@]}" -v "$ROOT:/repo:ro" -w /repo/institute-backend -e DATABASE_URL=sqlite:// -e PYTHONDONTWRITEBYTECODE=1 \
  -e PYTHONPATH=/repo/institute-backend/institute-paper-service:/repo/institute-backend/institute-common "${1:-institute-paper-service:latest}" \
  sh -c 'pip install -q --disable-pip-version-check pytest==8.3.3 >/dev/null 2>&1 && tesseract --version | head -1 && python -m pytest -q -p no:cacheprovider institute-paper-service/ocr_checks'
