#!/bin/bash
# Fixes ownership/mode on the host bind-mount directories the backend
# container writes to — factored out of deploy.sh so CI's docker-build job
# can exercise the exact same logic it's supposed to be validating, rather
# than a second, drift-prone copy of the same three commands.
#
# `mkdir -p` (on a fresh host, or a fresh MEDIA_HOST_PATH/STATIC_HOST_PATH
# value) creates a directory owned by whoever ran it (typically root) at
# mode 755 — the backend image's non-root `rastichat` user (fixed
# RASTICHAT_UID/RASTICHAT_GID, see backend/Dockerfile.prod) cannot write
# into that. This script closes that gap and is safe to run on every
# deploy: `chown`/`chmod` only ever change ownership/mode metadata on these
# two paths and their existing contents, never file contents, and never
# anything outside these two paths.
#
# 755 (not 750/700, not 777): these two directories exist specifically to
# be served publicly by the host-level Nginx in front of this stack
# (deploy/nginx/sites/backend.conf.template's /media/ and /static/
# aliases), which runs as its own host user (commonly www-data) with no
# membership in whatever group RASTICHAT_GID resolves to on this host —
# coupling this script to Nginx's runtime user/group would be its own
# source of fragility. 755 grants the owner (the container) full access
# and everyone else read+traverse only (never write), which is not
# "chmod 777" while still letting Nginx actually serve the files.
#
# Usage: scripts/staging/fix-permissions.sh <media-host-path> <static-host-path> [uid] [gid]
set -euo pipefail

MEDIA_HOST_PATH="${1:?Usage: fix-permissions.sh <media-host-path> <static-host-path> [uid] [gid]}"
STATIC_HOST_PATH="${2:?Usage: fix-permissions.sh <media-host-path> <static-host-path> [uid] [gid]}"
RASTICHAT_UID="${3:-${RASTICHAT_UID:-10001}}"
RASTICHAT_GID="${4:-${RASTICHAT_GID:-10001}}"

mkdir -p "$MEDIA_HOST_PATH" "$STATIC_HOST_PATH"
chown -R "${RASTICHAT_UID}:${RASTICHAT_GID}" "$MEDIA_HOST_PATH" "$STATIC_HOST_PATH"
chmod 755 "$MEDIA_HOST_PATH" "$STATIC_HOST_PATH"
echo "Owner ${RASTICHAT_UID}:${RASTICHAT_GID}, mode 755: ${MEDIA_HOST_PATH}, ${STATIC_HOST_PATH}"
