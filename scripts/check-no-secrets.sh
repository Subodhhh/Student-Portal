#!/usr/bin/env bash
# Fails if tracked files look like they contain secrets or if env files are tracked.
# Lightweight local/CI guard; it complements (does not replace) a dedicated scanner.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
status=0

tracked_env=$(git ls-files | grep -E '(^|/)\.env($|\.)' | grep -v '\.env\.example$' || true)
if [ -n "$tracked_env" ]; then echo "Tracked env files:"; echo "$tracked_env"; status=1; fi

patterns='(-----BEGIN [A-Z ]*PRIVATE KEY-----|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36}|xox[baprs]-[A-Za-z0-9-]{10,}|re_[A-Za-z0-9]{20,}|sk_live_[A-Za-z0-9]{16,}|postgres(ql)?(\+[a-z0-9]+)?://[^:/@ ]+:[^@ ]{6,}@[A-Za-z0-9.-]+)'
hits=$(git ls-files -z | grep -zvE '(package-lock\.json|\.png$|\.ico$)' | xargs -0 grep -nIE "$patterns" 2>/dev/null | grep -vE '(USERNAME|PASSWORD|RUNTIME_USER|MIGRATION_USER|example\.invalid|\.env\.example)' || true)
if [ -n "$hits" ]; then echo "Possible secrets:"; echo "$hits"; status=1; fi

[ $status -eq 0 ] && echo "No secrets or tracked env files found."
exit $status
