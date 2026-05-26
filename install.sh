#!/usr/bin/env bash
# Alertle-V2 installer
# Installs to /opt/alertle-v2 by default (override with INSTALL_DIR env var).
# Creates a dedicated system user and registers a systemd service.

set -euo pipefail

APP_NAME="alertle-v2"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${INSTALL_DIR:-/opt/alertle-v2}"
SERVICE_USER="alertle"
SERVICE_FILE="/etc/systemd/system/${APP_NAME}.service"
PORT=8888

echo "╔══════════════════════════════════════╗"
echo "║       Alertle-V2 Installer           ║"
echo "║   🐢 Slow turtle. Fast alerts.       ║"
echo "╚══════════════════════════════════════╝"
echo ""
echo "  Source : $SOURCE_DIR"
echo "  Install: $INSTALL_DIR"
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

# ── Copy files to install dir ─────────────────────────────────────────────────
mkdir -p "$INSTALL_DIR"
if [[ "$SOURCE_DIR" != "$INSTALL_DIR" ]]; then
    if command -v rsync &>/dev/null; then
        rsync -a --exclude='.git' --exclude='.venv' --exclude='__pycache__' \
              --exclude='*.pyc' --exclude='alertle_state.db' --exclude='config.yaml' \
              "$SOURCE_DIR/" "$INSTALL_DIR/"
    else
        cp -r "$SOURCE_DIR/." "$INSTALL_DIR/"
        # Clean up git artifacts in install dir
        rm -rf "$INSTALL_DIR/.git" "$INSTALL_DIR/.venv" 2>/dev/null || true
    fi
    echo "✅ Files copied to $INSTALL_DIR"
else
    echo "✅ Running from install dir — no copy needed"
fi

# ── System user ───────────────────────────────────────────────────────────────
if ! id -u "$SERVICE_USER" &>/dev/null; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
    echo "✅ Created system user: $SERVICE_USER"
else
    echo "✅ System user already exists: $SERVICE_USER"
fi

# ── Virtual environment ───────────────────────────────────────────────────────
VENV_DIR="$INSTALL_DIR/.venv"
if [[ ! -d "$VENV_DIR" ]]; then
    $PYTHON -m venv "$VENV_DIR"
    echo "✅ Virtual environment created"
fi
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"
echo "✅ Dependencies installed"

# ── Config setup ──────────────────────────────────────────────────────────────
if [[ ! -f "$INSTALL_DIR/config.yaml" ]]; then
    cp "$INSTALL_DIR/config.yaml.example" "$INSTALL_DIR/config.yaml"
    echo "✅ Created config.yaml from example — open http://localhost:$PORT to configure"
else
    echo "✅ config.yaml already exists — skipping"
fi

# ── Permissions ───────────────────────────────────────────────────────────────
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
echo "✅ Permissions set"

# ── Systemd service ───────────────────────────────────────────────────────────
cat > "$SERVICE_FILE" << EOF
[Unit]
Description=Alertle-V2 Sports Notification Service
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
ExecStart=$VENV_DIR/bin/uvicorn main:app --host 0.0.0.0 --port $PORT
Restart=on-failure
RestartSec=10
Environment=ALERTLE_CONFIG=$INSTALL_DIR/config.yaml

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
echo "║   Installed to: $INSTALL_DIR"
echo "║   Manage service:                    ║"
echo "║   systemctl status alertle-v2        ║"
echo "║   systemctl restart alertle-v2       ║"
echo "║   journalctl -u alertle-v2 -f        ║"
echo "╚══════════════════════════════════════╝"
