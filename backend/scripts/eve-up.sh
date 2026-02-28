#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(dirname "$SCRIPT_DIR")"
LOGS_DIR="$BACKEND_DIR/logs"

mkdir -p "$LOGS_DIR"

run_index=0
while [ -d "$LOGS_DIR/$run_index" ]; do
  run_index=$((run_index + 1))
done

RUN_DIR="$LOGS_DIR/$run_index"
mkdir -p "$RUN_DIR"

echo "Starting backend in run directory: $RUN_DIR"

export EVE_RUN_DIR="$RUN_DIR"
export EVE_LOG_LLM_IO="${EVE_LOG_LLM_IO:-true}"
export EVE_LOG_DB_THREADS="${EVE_LOG_DB_THREADS:-true}"
export EVE_LOG_TOOL_IO="${EVE_LOG_TOOL_IO:-true}"

BOOTSTRAP_PYTHON="${BOOTSTRAP_PYTHON:-}"
if [ -z "$BOOTSTRAP_PYTHON" ]; then
  if [ -x "$BACKEND_DIR/.venv/bin/python" ]; then
    BOOTSTRAP_PYTHON="$BACKEND_DIR/.venv/bin/python"
  elif command -v python3.11 >/dev/null 2>&1; then
    BOOTSTRAP_PYTHON="python3.11"
  elif command -v python3 >/dev/null 2>&1; then
    BOOTSTRAP_PYTHON="python3"
  else
    BOOTSTRAP_PYTHON="python"
  fi
fi

"$BOOTSTRAP_PYTHON" - <<PY
import datetime
import json
import pathlib
import os
run_dir = pathlib.Path(os.environ["EVE_RUN_DIR"])
meta = {
    "run_index": $run_index,
    "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "invoker": "backend/scripts/eve-up.sh",
}
(run_dir / "run_metadata.json").write_text(json.dumps(meta, indent=2))
PY

cd "$BACKEND_DIR"
PORT="${PORT:-8000}"
BOOTSTRAP_ENV_FILE="$RUN_DIR/bootstrap.env"

"$BOOTSTRAP_PYTHON" "$SCRIPT_DIR/eve_bootstrap.py" \
  --backend-dir "$BACKEND_DIR" \
  --run-dir "$RUN_DIR" \
  --emit-env-file "$BOOTSTRAP_ENV_FILE"

if [ ! -f "$BOOTSTRAP_ENV_FILE" ]; then
  echo "Missing bootstrap env file: $BOOTSTRAP_ENV_FILE" >&2
  exit 1
fi

# shellcheck source=/dev/null
source "$BOOTSTRAP_ENV_FILE"

if [ -z "${EVE_PYTHON_BIN:-}" ]; then
  echo "Bootstrap did not provide EVE_PYTHON_BIN" >&2
  exit 1
fi

unset PYTHONPATH
unset PYTHONHOME
export PYTHONNOUSERSITE="${PYTHONNOUSERSITE:-1}"

"$EVE_PYTHON_BIN" -m uvicorn app.main:app --reload --port "$PORT" 2>&1 | tee "$RUN_DIR/backend.log"
