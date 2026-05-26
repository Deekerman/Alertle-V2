#!/usr/bin/env bash
# Alertle-V2 installer
# Creates a dedicated system user, installs dependencies,
# and registers a systemd service named alertle-v2.

set -euo pipefail

APP_NAME="alertle-v2"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_USER="alertle"
SERVICE_FILE="/etc/systemd/system/${APP_NAME}.service"
PORT=8888

echo "╔══════════════════════════════════════╗"
echo "║       Alertle-V2 Installer           ║"
echo "║   🐢 Slow turtle. Fast alerts.       ║"
echo "╚══════════════════════════════════════╝"
echo ""

# ── Root check ────────────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    echo "❌ Please run as root: sudo bash install.sh"
    exit 1
fi

# ── Python check ──────────────────────────────────────────────────────────────
PYTHON=$(command -v python3 || true)
if [[ -z "$PYTHON" ]]; then
    echo "❌ Python 3 not found. Install it first."
    exit 1
fi
PYVER=$($PYTHON --version 2>&1 | awk '{print $2}')
echo "✅ Python $PYVER found"

# ── System user ───────────────────────────────────────────────────────────────
if ! id -u "$SERVICE_USER" &>/dev/null; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
    echo "✅ Created system user: $SERVICE_USER"
else
    echo "✅ System user already exists: $SERVICE_USER"
fi

# ── Virtual environment ───────────────────────────────────────────────────────
VENV_DIR="$APP_DIR/.venv"
if [[ ! -d "$VENV_DIR" ]]; then
    $PYTHON -m venv "$VENV_DIR"
    echo "✅ Virtual environment created"
fi
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
echo "✅ Dependencies installed"

# ── Config setup ──────────────────────────────────────────────────────────────
if [[ ! -f "$APP_DIR/config.yaml" ]]; then
    cp "$APP_DIR/config.yaml.example" "$APP_DIR/config.yaml"
    echo "✅ Created config.yaml from example — open http://localhost:$PORT to configure"
else
    echo "✅ config.yaml already exists — skipping"
fi

# ── Permissions ───────────────────────────────────────────────────────────────
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"
echo "✅ Permissions set"

# ── Systemd service ───────────────────────────────────────────────────────────
cat > "$SERVICE_FILE" << EOF
[Unit]
Description=Alertle-V2 Sports Notification Service
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$APP_DIR
ExecStart=$VENV_DIR/bin/uvicorn main:app --host 0.0.0.0 --port $PORT
Restart=on-failure
RestartSec=10
Environment=ALERTLE_CONFIG=$APP_DIR/config.yaml

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$APP_NAME"
systemctl restart "$APP_NAME"
echo "✅ Service registered and started: $APP_NAME"

echo ""
echo "╔══════════════════════════════════════╗"
echo "║   ✅ Alertle-V2 is running!          ║"
echo "║   Open: http://localhost:$PORT        ║"
echo "║                                      ║"
echo "║   Manage service:                    ║"
echo "║   systemctl status alertle-v2        ║"
echo "║   systemctl restart alertle-v2       ║"
echo "║   journalctl -u alertle-v2 -f        ║"
echo "╚══════════════════════════════════════╝"
