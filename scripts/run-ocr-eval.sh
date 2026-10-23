#!/usr/bin/env bash
# OCR accuracy/latency evaluation of photo papers (institute-paper-service/ocr_eval), inside the paper-service image (the only place
# Tesseract is installed). Uses this working tree's code mounted read-only, no network, nothing persistent touched. Writes the full
# per-question results as JSON to the given directory.
#   scripts/run-ocr-eval.sh [out_dir] [image] [extra run_eval args...]
#   e.g. scripts/run-ocr-eval.sh /tmp/ocr-eval institute-paper-service:latest --workers 6 --only regression,rotated
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
OUT="$(mkdir -p "${1:-/tmp/ocr-eval}" && cd "${1:-/tmp/ocr-eval}" && pwd)"
IMAGE="${2:-institute-paper-service:latest}"
shift $(( $# > 2 ? 2 : $# ))
docker run --rm --network none -u "$(id -u):$(id -g)" -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 -e DATABASE_URL=sqlite:// \
  -e PYTHONPATH=/repo/institute-backend/institute-paper-service:/repo/institute-backend/institute-common \
  -v "$ROOT:/repo:ro" -v "$OUT:/out" -w /tmp "$IMAGE" \
  python -m ocr_eval.run_eval --json /out/ocr_eval.json "$@"
