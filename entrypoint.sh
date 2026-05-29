#!/bin/sh
# Alertle-V2 entrypoint — run before uvicorn to ensure config.yaml is a file.
set -e

CONFIG_PATH="${ALERTLE_CONFIG:-/config/config.yaml}"
CONFIG_DIR="$(dirname "$CONFIG_PATH")"

# Docker creates a directory at the bind-mount target when the host file
# doesn't exist yet.  Detect and remove it so we can create a real file.
if [ -d "$CONFIG_PATH" ]; then
    echo "INFO: $CONFIG_PATH was a directory (Docker created it before the host file existed)."
    echo "INFO: Removing directory and creating an empty config file."
    rm -rf "$CONFIG_PATH"
fi

mkdir -p "$CONFIG_DIR"

# Seed an empty config if none exists so the first-start wizard can run.
if [ ! -f "$CONFIG_PATH" ]; then
    echo "# Alertle-V2 config — configure via the web UI at http://localhost:8888" > "$CONFIG_PATH"
fi

exec uvicorn main:app --host 0.0.0.0 --port 8888 "$@"
