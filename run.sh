#!/usr/bin/env bash
# Mitchit Quicknotes Studio v1.0 RTM — Ubuntu launcher (local only, no services).
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PYTHON="${MQ_PYTHON:-$ROOT/.venv/bin/python}"

if [[ ! -x "$PYTHON" ]]; then
    echo "[mitchit] local virtual environment missing · bootstrapping .venv" >&2
    python3 -m venv "$ROOT/.venv"
    "$ROOT/.venv/bin/python" -m pip install --quiet --upgrade pip
    "$ROOT/.venv/bin/python" -m pip install --quiet -r "$ROOT/requirements.txt"
    PYTHON="$ROOT/.venv/bin/python"
fi

if [[ -z "${QT_QPA_PLATFORM:-}" && -z "${DISPLAY:-}" && -z "${WAYLAND_DISPLAY:-}" ]]; then
    export QT_QPA_PLATFORM=offscreen
    echo "[mitchit] no display server detected · using offscreen platform" >&2
fi

exec "$PYTHON" -m mitchit_quicknotes "$@"
