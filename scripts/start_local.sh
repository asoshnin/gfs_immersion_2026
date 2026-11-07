#!/usr/bin/env bash
# GFS Immersion Hub — local development startup
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HUB_DIR="$(dirname "$SCRIPT_DIR")"
WORKSPACE_ROOT="$(cd "$HUB_DIR/../../.." && pwd)"

# Ensure FTS5 database is accessible
DB_PATH="$HUB_DIR/../data/gfs_immersion_2026.sqlite"
if [ ! -f "$DB_PATH" ]; then
    echo "⚠ FTS5 database not found at $DB_PATH"
    echo "  Building index..."
    python3 "$HUB_DIR/../scripts/build_fts5_index.py"
fi

echo "🚀 Starting GFS Immersion Hub..."
echo "   URL: http://localhost:8080"
echo "   DB:  $DB_PATH"
echo ""

cd "$HUB_DIR"
exec python3 serve.py --port 8080