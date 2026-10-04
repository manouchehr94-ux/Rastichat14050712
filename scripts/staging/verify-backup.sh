#!/bin/bash
# Verifies a backup produced by backup.sh is intact: checksum matches and
# the gzip/tar stream isn't corrupt. Does NOT touch any running database —
# restore.sh is what actually proves a backup is restorable, and should be
# run against a disposable copy periodically (see docs/runbooks/BACKUP_RESTORE.md).
#
# Given a DB backup file (rastichat-db-<timestamp>.sql.gz), also looks for
# that timestamp's rastichat-meta-<timestamp>.json alongside it and, if it
# names a media backup, verifies that archive too — a backup "set" from one
# run of backup.sh is one unit to trust, not two files an operator has to
# remember to check separately.
#
# Usage: scripts/staging/verify-backup.sh <path-to-backup-file.sql.gz|.tar.gz>
set -euo pipefail

FILE="${1:?Usage: verify-backup.sh <path-to-rastichat-db-or-media-backup-file>}"

verify_one() {
  local file="$1"
  local checksum_file="${file}.sha256"

  if [ ! -f "$file" ]; then
    echo "Backup file not found: $file" >&2
    return 1
  fi
  if [ ! -f "$checksum_file" ]; then
    echo "No checksum file found alongside it: $checksum_file" >&2
    return 1
  fi

  echo "--- Verifying checksum: $(basename "$file") ---"
  if (cd "$(dirname "$file")" && sha256sum -c "$(basename "$checksum_file")"); then
    echo "Checksum OK."
  else
    echo "CHECKSUM MISMATCH — this backup file is corrupt or was tampered with. Do not restore it." >&2
    return 1
  fi

  echo "--- Verifying archive integrity: $(basename "$file") ---"
  case "$file" in
    *.sql.gz) gzip -t "$file" && echo "gzip stream OK." ;;
    *.tar.gz) tar -tzf "$file" >/dev/null && echo "tar archive OK." ;;
    *) echo "Unrecognized backup file extension — skipping format-specific check." ;;
  esac
  echo
}

overall_ok=0
verify_one "$FILE" || overall_ok=1

# Auto-discover a sibling media backup via the same-timestamp meta.json —
# only attempted for a DB backup filename (verifying a media file directly
# just verifies that one file, same as before this existed).
case "$(basename "$FILE")" in
  rastichat-db-*.sql.gz)
    TIMESTAMP="$(basename "$FILE" | sed -E 's/^rastichat-db-(.+)\.sql\.gz$/\1/')"
    META_FILE="$(dirname "$FILE")/rastichat-meta-${TIMESTAMP}.json"
    if [ -f "$META_FILE" ]; then
      MEDIA_BACKUP_FILE="$(python3 -c "
import json, sys
with open('$META_FILE') as f:
    data = json.load(f)
name = data.get('media_backup_file')
print(name if name else '')
" 2>/dev/null || true)"
      if [ -n "$MEDIA_BACKUP_FILE" ]; then
        echo "=== Also verifying media backup named in $(basename "$META_FILE"): ${MEDIA_BACKUP_FILE} ==="
        verify_one "$(dirname "$FILE")/${MEDIA_BACKUP_FILE}" || overall_ok=1
      else
        echo "(${META_FILE##*/} lists no media backup for this timestamp — nothing else to verify.)"
      fi
    else
      echo "(No $(basename "$META_FILE") alongside this file — skipping media-backup auto-discovery.)"
    fi
    ;;
esac

if [ "$overall_ok" -ne 0 ]; then
  echo "FAIL: one or more backup files failed verification — see above." >&2
  exit 1
fi

echo "Backup verified: $FILE"
