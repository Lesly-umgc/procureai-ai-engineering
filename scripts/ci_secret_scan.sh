#!/usr/bin/env bash
# ci_secret_scan.sh — fail the build if anything looking like a real
# credential is committed. Runs in GitHub Actions and locally.
#
# Patterns are deliberately narrow (known token prefixes + high-entropy
# quoted assignments) so env-var *names* like GEMINI_API_KEY don't trip it.
set -euo pipefail
cd "$(dirname "$0")/.."

GREP_ARGS=( -rEI --exclude-dir=.git --exclude-dir=.venv --exclude-dir=__pycache__ --exclude='*.pyc' )

TOKEN_PATTERNS=(
  'ghp_[A-Za-z0-9]{20,}'
  'gho_[A-Za-z0-9]{20,}'
  'github_pat_[A-Za-z0-9_]{20,}'
  'AIza[0-9A-Za-z_-]{30,}'
  'sk-[A-Za-z0-9]{20,}'
  'xox[baprs]-[A-Za-z0-9-]{10,}'
)

fail=0

for pat in "${TOKEN_PATTERNS[@]}"; do
  matches=$(grep "${GREP_ARGS[@]}" -n "$pat" . || true)
  if [ -n "$matches" ]; then
    echo "SECRET SCAN FAILURE — token pattern '$pat' matched:"
    echo "$matches"
    fail=1
  fi
done

# Quoted high-entropy assignments, e.g. api_key = "sk-...." (env lookups and
# placeholders excluded so legitimate config code passes).
generic=$(grep "${GREP_ARGS[@]}" -n \
  "(api[_-]?key|secret|password)\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}['\"]" . \
  | grep -viE "getenv|environ|os\.get|example|placeholder|redacted|<redacted>" || true)
if [ -n "$generic" ]; then
  echo "SECRET SCAN FAILURE — suspicious credential assignment:"
  echo "$generic"
  fail=1
fi

if [ "$fail" -eq 1 ]; then
  echo "secret scan FAILED" >&2
  exit 1
fi
echo "secret scan clean"
