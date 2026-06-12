#!/bin/bash
# send-git-request.sh — Generate a correctly-formatted git-request message.
#
# Usage:
#   bash send-git-request.sh \
#     --task-id task-YYYYMMDD-HHMMSS \
#     --workspace /root/hiclaw-fs/shared/tasks/{task-id}/workspace \
#     --ops "git clone ..." \
#     --context "Why you need these operations"
#
# The script outputs the EXACT text to send in the Worker room.
# Copy the output and send it — @manager is already included, you do NOT
# need to remember or construct the mention yourself.
#
# The script also writes .git-request to the task directory so the
# Manager's heartbeat can discover it if the @mention is missed.

set -euo pipefail

TASK_ID=""
WORKSPACE=""
OPS=""
CONTEXT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --task-id) TASK_ID="$2"; shift 2 ;;
    --workspace) WORKSPACE="$2"; shift 2 ;;
    --ops) OPS="$2"; shift 2 ;;
    --context) CONTEXT="$2"; shift 2 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$TASK_ID" || -z "$WORKSPACE" || -z "$OPS" ]]; then
  echo "ERROR: --task-id, --workspace, and --ops are required" >&2
  echo "Usage: $0 --task-id T --workspace W --ops O [--context C]" >&2
  exit 1
fi

DOMAIN="${HICLAW_MATRIX_DOMAIN:-matrix-local.hiclaw.io}"
MANAGER="@manager:${DOMAIN}"

# Build the message
MESSAGE="${MANAGER} ${TASK_ID} git-request:
workspace: ${WORKSPACE}
operations:"

# Indent each operation line
while IFS= read -r op; do
  op=$(echo "$op" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
  [[ -z "$op" ]] && continue
  MESSAGE="${MESSAGE}
  - ${op}"
done <<< "$OPS"

if [[ -n "$CONTEXT" ]]; then
  MESSAGE="${MESSAGE}
---CONTEXT---
${CONTEXT}
---END---"
fi

# Write to task directory for heartbeat recovery
TASK_DIR="/root/hiclaw-fs/shared/tasks/${TASK_ID}"
if [[ -d "$TASK_DIR" ]]; then
  echo "$MESSAGE" > "${TASK_DIR}/.git-request"
fi

# Output the message — Worker LLM should send this text in the room
echo "$MESSAGE"
