#!/bin/bash
# =============================================================================
# VS Code Server Setup Script
# Tested on Ubuntu 22.04 LTS
#
# Provides TWO ways to access this codebase remotely in VS Code:
#
#   Option A — VS Code Remote SSH (recommended)
#     Install the "Remote - SSH" extension in your local VS Code,
#     then connect to this machine via SSH. Full VS Code experience,
#     no browser needed.
#
#   Option B — code-server (browser-based)
#     Access VS Code in any browser at http://<machine-ip>:8080
#     Useful when you don't have VS Code installed locally.
#
# Usage:
#   chmod +x setup-vscode-server.sh
#   ./setup-vscode-server.sh [--ssh-only | --code-server-only]
#
# =============================================================================

set -euo pipefail

# ── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BLUE='\033[0;34m'; NC='\033[0m'

log()  { echo -e "${CYAN}[$(date +%H:%M:%S)]${NC} $*"; }
ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC}  $*"; }
die()  { echo -e "${RED}✗ ERROR:${NC} $*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="${1:-both}"   # --ssh-only | --code-server-only | both

echo -e "${BLUE}"
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║               VS Code Server Setup Script                   ║"
echo "║            Remote SSH  ·  code-server (browser)             ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# ── Detect machine IP ─────────────────────────────────────────────────────────
MACHINE_IP=$(hostname -I | awk '{print $1}')
CURRENT_USER=$(whoami)
log "Machine IP : ${MACHINE_IP}"
log "User       : ${CURRENT_USER}"
log "Project dir: ${SCRIPT_DIR}"
echo ""

# =============================================================================
# OPTION A — VS Code Remote SSH
# =============================================================================
setup_ssh() {
    log "[SSH] Setting up OpenSSH server for VS Code Remote SSH..."

    sudo apt-get update -qq
    sudo apt-get install -y openssh-server curl ufw 2>/dev/null

    # Enable and start SSH
    sudo systemctl enable ssh
    sudo systemctl start ssh

    # Allow SSH through firewall (if ufw is active)
    if sudo ufw status | grep -q "Status: active"; then
        sudo ufw allow OpenSSH
        ok "Firewall: SSH allowed"
    fi

    # Print SSH config location for reference
    SSH_CONFIG="/etc/ssh/sshd_config"
    ok "SSH server running on port 22"

    # ── Authorized keys setup ─────────────────────────────────────────────
    echo ""
    echo -e "${YELLOW}  To connect from your local machine:${NC}"
    echo ""
    echo "  1. (On your LOCAL machine) Copy your SSH public key to this machine:"
    echo "       ssh-copy-id ${CURRENT_USER}@${MACHINE_IP}"
    echo "     Or manually paste your public key into:"
    echo "       ~/.ssh/authorized_keys"
    echo ""
    echo "  2. Install the 'Remote - SSH' extension in VS Code (local):"
    echo "       Extension ID: ms-vscode-remote.remote-ssh"
    echo ""
    echo "  3. In VS Code: Ctrl+Shift+P → 'Remote-SSH: Connect to Host...'"
    echo "       Enter: ${CURRENT_USER}@${MACHINE_IP}"
    echo ""
    echo "  4. Once connected, open the project folder:"
    echo "       ${SCRIPT_DIR}"
    echo ""

    # ── Optional: add entry to local SSH config ───────────────────────────
    echo -e "${CYAN}  Suggested ~/.ssh/config entry (add on your LOCAL machine):${NC}"
    echo ""
    echo "  Host kubeddos-vm"
    echo "      HostName ${MACHINE_IP}"
    echo "      User     ${CURRENT_USER}"
    echo "      ForwardAgent yes"
    echo ""
}

# =============================================================================
# OPTION B — code-server (browser-based VS Code)
# =============================================================================
setup_code_server() {
    log "[code-server] Installing code-server..."

    # Install code-server via official install script
    curl -fsSL https://code-server.dev/install.sh | sh

    ok "code-server installed: $(code-server --version 2>/dev/null | head -1)"

    # ── Configure ─────────────────────────────────────────────────────────
    CONFIG_DIR="${HOME}/.config/code-server"
    mkdir -p "$CONFIG_DIR"

    # Generate a random password if config doesn't exist
    if [[ ! -f "${CONFIG_DIR}/config.yaml" ]]; then
        PASS=$(openssl rand -base64 16)
        cat > "${CONFIG_DIR}/config.yaml" <<EOF
bind-addr: 0.0.0.0:8080
auth: password
password: ${PASS}
cert: false
EOF
        ok "code-server config created at ${CONFIG_DIR}/config.yaml"
        echo ""
        echo -e "  ${YELLOW}Your code-server password:${NC} ${GREEN}${PASS}${NC}"
        echo "  (Also saved in ${CONFIG_DIR}/config.yaml)"
    else
        ok "code-server config already exists at ${CONFIG_DIR}/config.yaml"
        PASS=$(grep "^password:" "${CONFIG_DIR}/config.yaml" | awk '{print $2}')
        echo -e "  ${YELLOW}Existing password:${NC} ${GREEN}${PASS}${NC}"
    fi

    # ── Firewall ──────────────────────────────────────────────────────────
    if sudo ufw status 2>/dev/null | grep -q "Status: active"; then
        sudo ufw allow 8080/tcp
        ok "Firewall: port 8080 allowed"
    fi

    # ── Systemd service ───────────────────────────────────────────────────
    log "Creating systemd service for code-server..."

    sudo bash -c "cat > /etc/systemd/system/code-server.service" <<EOF
[Unit]
Description=VS Code Server (code-server)
After=network.target

[Service]
Type=simple
User=${CURRENT_USER}
WorkingDirectory=${SCRIPT_DIR}
ExecStart=$(command -v code-server) --config ${CONFIG_DIR}/config.yaml ${SCRIPT_DIR}
Restart=on-failure
RestartSec=5
Environment=HOME=${HOME}

[Install]
WantedBy=multi-user.target
EOF

    sudo systemctl daemon-reload
    sudo systemctl enable code-server
    sudo systemctl restart code-server
    sleep 2

    if systemctl is-active --quiet code-server; then
        ok "code-server service running"
    else
        warn "code-server service may not have started. Check: sudo journalctl -u code-server -n 30"
    fi

    # ── Install recommended extensions ────────────────────────────────────
    log "Installing VS Code extensions..."
    EXTENSIONS=(
        "ms-python.python"
        "ms-python.vscode-pylance"
        "redhat.vscode-yaml"
        "ms-kubernetes-tools.vscode-kubernetes-tools"
        "ms-azuretools.vscode-docker"
        "hashicorp.terraform"
        "eamodio.gitlens"
    )
    for ext in "${EXTENSIONS[@]}"; do
        code-server --install-extension "$ext" &>/dev/null \
            && ok "Extension: $ext" \
            || warn "Could not install extension: $ext"
    done

    echo ""
    echo -e "  ${GREEN}Access code-server in your browser:${NC}"
    echo ""
    echo "    URL      : http://${MACHINE_IP}:8080"
    echo "    Password : ${PASS}"
    echo ""
    echo -e "  ${CYAN}Service commands:${NC}"
    echo "    sudo systemctl status  code-server"
    echo "    sudo systemctl restart code-server"
    echo "    sudo systemctl stop    code-server"
    echo "    sudo journalctl -u code-server -f"
    echo ""
}

# =============================================================================
# Run selected options
# =============================================================================
case "$MODE" in
    --ssh-only)
        setup_ssh
        ;;
    --code-server-only)
        setup_code_server
        ;;
    *)
        setup_ssh
        echo ""
        echo -e "${BLUE}────────────────────────────────────────────────────────────────${NC}"
        echo ""
        setup_code_server
        ;;
esac

# =============================================================================
# Final summary
# =============================================================================
echo ""
echo -e "${BLUE}╔══════════════════════════════════════════════════════════════╗"
echo -e "║               Remote Access Summary                         ║"
echo -e "╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${GREEN}Option A — VS Code Remote SSH${NC} (best experience)"
echo    "    Install extension : ms-vscode-remote.remote-ssh"
echo    "    Connect to        : ${CURRENT_USER}@${MACHINE_IP}"
echo    "    Open folder       : ${SCRIPT_DIR}"
echo ""
echo -e "  ${GREEN}Option B — Browser (code-server)${NC}"
echo    "    URL               : http://${MACHINE_IP}:8080"
if [[ -f "${HOME}/.config/code-server/config.yaml" ]]; then
    PASS=$(grep "^password:" "${HOME}/.config/code-server/config.yaml" | awk '{print $2}')
    echo    "    Password          : ${PASS}"
fi
echo ""
echo -e "  ${YELLOW}Note:${NC} If accessing over the internet (not LAN), use SSH tunnelling:"
echo    "    ssh -L 8080:localhost:8080 ${CURRENT_USER}@<public-ip>"
echo    "    Then open: http://localhost:8080"
echo ""
