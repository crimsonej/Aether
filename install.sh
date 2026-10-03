#!/bin/bash
# Aether Global Installer (enhanced)

set -e

AETHER_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
INSTALL_DIR="$HOME/.aether/runtime"
BIN_DIR="$HOME/.local/bin"
SYSTEMD_UNIT="$HOME/.config/systemd/user/aether.service"

echo "🚀 Installing Aether globally..."

# 1. Create runtime and bin directories
mkdir -p "$INSTALL_DIR"
mkdir -p "$BIN_DIR"

# 2. Create dedicated virtual environment
echo "📦 Creating dedicated runtime environment..."
python3 -m venv "$INSTALL_DIR"

# 3. Install Aether in editable mode inside the venv
echo "⚙️ Installing package..."
"$INSTALL_DIR/bin/pip" install --upgrade pip
"$INSTALL_DIR/bin/pip" install -e "$AETHER_ROOT"

# 4. Symlink the CLI binary to ~/.local/bin (overwrite if exists)
echo "🔗 Linking binary to $BIN_DIR..."
ln -sf "$INSTALL_DIR/bin/aether" "$BIN_DIR/aether"

# 5. Ensure $BIN_DIR is in PATH – add to ~/.profile if missing (idempotent)
SHELL_PROFILE="$HOME/.profile"
if ! grep -q "$BIN_DIR" "$SHELL_PROFILE"; then
    echo "export PATH=\"$BIN_DIR:\$PATH\"" >> "$SHELL_PROFILE"
    echo "✓ Added $BIN_DIR to $SHELL_PROFILE"
else
    echo "✓ $BIN_DIR already present in $SHELL_PROFILE"
fi

# 6. Offer to create a user‑level systemd service (no sudo required)
read -p "Create a user‑level systemd service for Aether? [y/N] " -r CREATE_SERVICE
if [[ "$CREATE_SERVICE" =~ ^[Yy]$ ]]; then
    mkdir -p "$(dirname "$SYSTEMD_UNIT")"
    cat > "$SYSTEMD_UNIT" <<EOF
[Unit]
Description=Aether Platform Service
After=network.target

[Service]
Type=simple
ExecStart=$BIN_DIR/aether start
Restart=on-failure
Environment=PATH=$BIN_DIR:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable --now aether.service
    echo "✅ Systemd service created and started."
else
    echo "ℹ️ Skipping systemd service creation."
fi

# 7. Optional Docker‑Compose generation (no docker actions performed)
read -p "Generate a Docker‑Compose skeleton? [y/N] " -r CREATE_DOCKER
if [[ "$CREATE_DOCKER" =~ ^[Yy]$ ]]; then
    cat > docker-compose.yml <<'DOCKYML'
version: "3.8"
services:
  aether-gateway:
    image: aether:latest
    build: .
    ports:
      - "18791:18791"
    volumes:
      - ./data:/app/data
      - ./config:/app/config
    environment:
      - PYTHONUNBUFFERED=1
    restart: unless-stopped
DOCKYML
    echo "✅ docker-compose.yml created."
else
    echo "ℹ️ Skipping Docker‑Compose generation."
fi

# 8. Final instructions – no need to source manually because ~/.profile is read on login.
echo "\n✅ Aether installed successfully!"
echo "You may need to log out and back in for PATH changes to take effect."

# This script handles the creation of a dedicated venv, symlinking, and PATH configuration.

set -e

AETHER_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
INSTALL_DIR="$HOME/.aether/runtime"
BIN_DIR="$HOME/.local/bin"

echo "🚀 Installing Aether globally..."

# 1. Create Aether runtime directory
mkdir -p "$INSTALL_DIR"
mkdir -p "$BIN_DIR"

# 2. Create dedicated virtual environment
echo "📦 Creating dedicated runtime environment..."
python3 -m venv "$INSTALL_DIR"

# 3. Install Aether in editable mode in the runtime env
echo "⚙️ Installing package..."
"$INSTALL_DIR/bin/pip" install --upgrade pip
"$INSTALL_DIR/bin/pip" install -e "$AETHER_ROOT"

# 4. Symlink the CLI binary to ~/.local/bin
echo "🔗 Linking binary to $BIN_DIR..."
ln -sf "$INSTALL_DIR/bin/aether" "$BIN_DIR/aether"

# 5. Automatically update PATH in shell config
echo "📝 Configuring shell PATH..."
SHELL_CONFIG=""
if [ -f "$HOME/.zshrc" ]; then
    SHELL_CONFIG="$HOME/.zshrc"
elif [ -f "$HOME/.bashrc" ]; then
    SHELL_CONFIG="$HOME/.bashrc"
fi

if [ -n "$SHELL_CONFIG" ]; then
    if ! grep -q "$BIN_DIR" "$SHELL_CONFIG"; then
        echo "" >> "$SHELL_CONFIG"
        echo "# Aether Platform PATH" >> "$SHELL_CONFIG"
        echo "export PATH=\"$BIN_DIR:\$PATH\"" >> "$SHELL_CONFIG"
        echo "✓ Added $BIN_DIR to $SHELL_CONFIG"
    else
        echo "✓ PATH already configured in $SHELL_CONFIG"
    fi
else
    echo "⚠️ Could not find .zshrc or .bashrc. Please add $BIN_DIR to your PATH manually."
fi

echo ""
echo "✅ Aether installed successfully!"
echo "👉 PLEASE RUN: source ~/.zshrc (or restart your terminal)"
echo "Then you can simply run 'aether' from anywhere!"

