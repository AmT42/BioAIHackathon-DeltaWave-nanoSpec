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
export REPL_CONTROLLED_RELOAD_ENABLED="${REPL_CONTROLLED_RELOAD_ENABLED:-false}"
export REPL_CONTROLLED_RELOAD_EXIT_CODE="${REPL_CONTROLLED_RELOAD_EXIT_CODE:-75}"
export REPL_CONTROLLED_RELOAD_DELAY_MS="${REPL_CONTROLLED_RELOAD_DELAY_MS:-350}"

python3 - <<PY
import datetime
import json
import pathlib
import os
run_dir = pathlib.Path(os.environ["EVE_RUN_DIR"])
meta = {
    "run_index": $run_index,
    "created_at": datetime.datetime.utcnow().isoformat() + "Z",
    "invoker": "backend/scripts/eve-up.sh",
}
(run_dir / "run_metadata.json").write_text(json.dumps(meta, indent=2))
PY

cd "$BACKEND_DIR"
PORT="${PORT:-8000}"
USE_FILE_WATCH_RELOAD="${EVE_USE_FILE_WATCH_RELOAD:-false}"
: > "$RUN_DIR/backend.log"

if [[ "$USE_FILE_WATCH_RELOAD" == "true" ]]; then
  echo "Starting in watch-reload mode (--reload)." | tee -a "$RUN_DIR/backend.log"
  uvicorn app.main:app --reload --port "$PORT" 2>&1 | tee -a "$RUN_DIR/backend.log"
  exit "${PIPESTATUS[0]}"
fi

echo "Starting in controlled-reload mode (turn-boundary safe)." | tee -a "$RUN_DIR/backend.log"
while true; do
  set +e
  uvicorn app.main:app --port "$PORT" 2>&1 | tee -a "$RUN_DIR/backend.log"
  status="${PIPESTATUS[0]}"
  set -e

  if [[ "$REPL_CONTROLLED_RELOAD_ENABLED" == "true" && "$status" -eq "$REPL_CONTROLLED_RELOAD_EXIT_CODE" ]]; then
    echo "Controlled reload requested (exit code $status); restarting backend process..." | tee -a "$RUN_DIR/backend.log"
    continue
  fi

  exit "$status"
done
