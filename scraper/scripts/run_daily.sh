#!/usr/bin/env bash
# Daily catalog download for cron/systemd. Exit code: 0 ok, 1 partial, 2 failed.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/scraper-$(date +%F).log"

exec 9>"$LOG_DIR/.lock"
if ! flock -n 9; then
    echo "$(date -Is) another run is still in progress, skipping" >>"$LOG_FILE"
    exit 0
fi

status=0
uv run --frozen supermarket-scraper run >>"$LOG_FILE" 2>&1 || status=$?

find "$LOG_DIR" -name 'scraper-*.log' -mtime +30 -delete
exit "$status"
