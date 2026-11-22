#!/bin/bash
set -euo pipefail

# Remote sessions only: a local developer manages their own venv.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

VENV="$CLAUDE_PROJECT_DIR/.venv"
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r requirements-dev.txt -e .

if ! command -v ffmpeg >/dev/null; then
  echo "ffmpeg is missing: the tests build their audio files with it." >&2
fi

if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo "export PATH=\"$VENV/bin:\$PATH\"" >> "$CLAUDE_ENV_FILE"
fi
