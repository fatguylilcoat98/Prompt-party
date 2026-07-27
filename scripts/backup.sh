#!/usr/bin/env bash
# Prompt Party backup: consistent SQLite snapshot + media/exports archive.
# Usage: scripts/backup.sh [backup_dir]   (default ./backups)
# Cron example (02:30 nightly):
#   30 2 * * * /opt/prompt-party/scripts/backup.sh /opt/prompt-party/backups
set -euo pipefail
cd "$(dirname "$0")/.."

BACKUP_DIR="${1:-./backups}"
STAMP="$(date +%Y%m%d-%H%M%S)"
KEEP=14

DB_PATH="${PROMPT_PARTY_DATABASE_URL:-sqlite:///./data/prompt_party.db}"
DB_PATH="${DB_PATH#sqlite:///}"
MEDIA_DIR="${PROMPT_PARTY_MEDIA_DIR:-./media}"
EXPORTS_DIR="${PROMPT_PARTY_EXPORTS_DIR:-./exports}"

mkdir -p "$BACKUP_DIR"

PY=".venv/bin/python"; [ -x "$PY" ] || PY="python3"

if [ -f "$DB_PATH" ]; then
    # sqlite3 online-backup API: consistent even while the app is live.
    "$PY" - "$DB_PATH" "$BACKUP_DIR/db-$STAMP.sqlite" <<'EOF'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1])
dst = sqlite3.connect(sys.argv[2])
with dst:
    src.backup(dst)
dst.close(); src.close()
print(f"database snapshot -> {sys.argv[2]}")
EOF
else
    echo "no database at $DB_PATH (nothing recorded yet)"
fi

tar -czf "$BACKUP_DIR/files-$STAMP.tar.gz" \
    --ignore-failed-read "$MEDIA_DIR" "$EXPORTS_DIR" 2>/dev/null || true
echo "media/exports archive -> $BACKUP_DIR/files-$STAMP.tar.gz"

# Retention: keep the newest $KEEP of each kind.
for pattern in "db-*.sqlite" "files-*.tar.gz"; do
    ls -1t "$BACKUP_DIR"/$pattern 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm --
done
echo "backup complete ($(ls "$BACKUP_DIR" | wc -l) files retained in $BACKUP_DIR)"
