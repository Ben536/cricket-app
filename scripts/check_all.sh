#!/usr/bin/env bash
#
# Run every gate the project has, locally, in one command - the same set CI
# runs, so "green here" means "green there". Exit code is non-zero if ANY gate
# fails; each gate's verdict is printed so a partial failure is obvious.
#
#   ./scripts/check_all.sh          # everything
#   ./scripts/check_all.sh --fast   # skip the slow ones (parity, build)
#
# Toolchain expectations (see CLAUDE.md "Start here"):
#   - Python: a venv at .venv (uv venv --python 3.11 .venv && uv pip install
#     --python .venv/bin/python -r requirements-dev.txt), or python3 on PATH
#   - Node 22 with node_modules installed (npm ci)
#
set -u
cd "$(dirname "$0")/.."

FAST=0
[ "${1:-}" = "--fast" ] && FAST=1

if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=python3; fi

pass=0; fail=0; results=()
run_gate() {
  local name="$1"; shift
  echo ""
  echo "=== $name ==="
  if "$@"; then
    results+=("PASS  $name"); pass=$((pass+1))
  else
    results+=("FAIL  $name"); fail=$((fail+1))
  fi
}

# --- Python -----------------------------------------------------------------
run_gate "pytest"            "$PY" -m pytest tests/ -q
run_gate "codebase drift"    "$PY" tools/codebase_map.py --check --quiet
run_gate "engine tests"      "$PY" -m pytest tests/test_engine.py -q

# --- Frontend ---------------------------------------------------------------
run_gate "tsc"               npx tsc -b
run_gate "tsc (tests)"       npx tsc -p tsconfig.test.json
run_gate "eslint"            npx eslint src tools/parity
run_gate "vitest"            npx vitest run

if [ $FAST -eq 0 ]; then
  run_gate "vite build"      npx vite build
  # --- Cross-engine parity ---------------------------------------------------
  parity() {
    "$PY" tools/parity/gen_shots.py >/dev/null &&
    git diff --quiet --exit-code -- tools/parity/shots.json &&
    "$PY" tools/parity/run_py.py >/dev/null &&
    npx tsx tools/parity/run_ts.ts >/dev/null &&
    "$PY" tools/parity/compare.py
  }
  run_gate "engine parity"   parity
fi

# --- Ops sanity ------------------------------------------------------------
if command -v shellcheck >/dev/null 2>&1; then
  run_gate "shellcheck"      shellcheck -S warning scripts/*.sh
else
  echo ""; echo "(shellcheck not installed - skipped; CI runs it)"
fi

# --- Summary ----------------------------------------------------------------
echo ""
echo "================ SUMMARY ================"
printf '%s\n' "${results[@]}"
echo "-----------------------------------------"
echo "$pass passed, $fail failed"
[ $fail -eq 0 ]
