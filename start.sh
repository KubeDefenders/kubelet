#!/usr/bin/env bash
# =============================================================================
# KubeDDoS — Start Script
# Brings up the cluster (if needed), required services, and the frontend.
#
# Usage:
#   chmod +x start.sh && ./start.sh
#
# Opens:  http://localhost:5001/experiment
# Logs:   /tmp/kubectl-proxy.log
#         /tmp/prom-portforward.log
#         /tmp/kubeddos-attacks.log
# =============================================================================

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$PROJECT_ROOT/.venv"

ok()   { echo -e "${GREEN}  ✓ $*${NC}"; }
info() { echo -e "${CYAN}  → $*${NC}"; }
warn() { echo -e "${YELLOW}  ⚠ $*${NC}"; }
die()  { echo -e "${RED}  ✗ $*${NC}"; exit 1; }

# wait_for URL LABEL TIMEOUT_SECS
# Polls URL every second until it responds, timeout is reached, or PID dies.
# Usage: wait_for URL LABEL TIMEOUT_SECS [PID]
wait_for() {
    local url="$1" label="$2" timeout="${3:-30}" pid="${4:-}"
    local elapsed=0
    printf "  ${CYAN}→ Waiting for %s" "$label"
    while ! curl -sf "$url" > /dev/null 2>&1; do
        # If a PID was given and it has already exited, stop waiting
        if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
            echo -e "${NC}"
            return 2  # distinct code: process died
        fi
        if [ "$elapsed" -ge "$timeout" ]; then
            echo -e "${NC}"
            return 1
        fi
        printf "."
        sleep 1
        (( elapsed++ )) || true
    done
    echo -e "${NC}"
    return 0
}

# wait_for_kubectl TIMEOUT_SECS
# Waits until the K8s API server is reachable via kubectl.
wait_for_kubectl() {
    local timeout="${1:-60}" elapsed=0
    printf "  ${CYAN}→ Waiting for K8s API server"
    while ! kubectl get nodes --no-headers > /dev/null 2>&1; do
        if [ "$elapsed" -ge "$timeout" ]; then
            echo -e "${NC}"
            return 1
        fi
        printf "."
        sleep 1
        (( elapsed++ )) || true
    done
    echo -e "${NC}"
    return 0
}

echo -e "${CYAN}"
echo "  ██╗  ██╗██╗   ██╗██████╗ ███████╗██████╗ ██████╗  ██████╗ ███████╗"
echo "  ██║ ██╔╝██║   ██║██╔══██╗██╔════╝██╔══██╗██╔══██╗██╔═══██╗██╔════╝"
echo "  █████╔╝ ██║   ██║██████╔╝█████╗  ██║  ██║██║  ██║██║   ██║███████╗"
echo "  ██╔═██╗ ██║   ██║██╔══██╗██╔══╝  ██║  ██║██║  ██║██║   ██║╚════██║"
echo "  ██║  ██╗╚██████╔╝██████╔╝███████╗██████╔╝██████╔╝╚██████╔╝███████║"
echo "  ╚═╝  ╚═╝ ╚═════╝ ╚═════╝ ╚══════╝╚═════╝ ╚═════╝  ╚═════╝ ╚══════╝"
echo -e "${NC}"
echo "  KubeDDoS Start Script"
echo ""

# ── Step 1: Minikube ──────────────────────────────────────────────────────────
echo -e "${YELLOW}[1/5] Checking Minikube cluster...${NC}"
if minikube status 2>/dev/null | grep -q "host: Running"; then
    ok "Minikube already running ($(minikube ip))"
else
    info "Starting Minikube..."
    minikube start --driver=docker
    ok "Minikube started ($(minikube ip))"
fi

# Wait for the API server to accept connections before any kubectl calls
if ! wait_for_kubectl 60; then
    die "K8s API server not reachable after 60s — check: minikube status"
fi

# Enable metrics-server so HPAs can read CPU/memory metrics
minikube addons enable metrics-server 2>/dev/null || true

MINIKUBE_IP=$(minikube ip)
NODE_PORT=$(kubectl get svc -n sock-shop front-end -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || echo "30001")
TARGET_URL="http://${MINIKUBE_IP}:${NODE_PORT}"

# ── Step 2: Sock Shop pods ────────────────────────────────────────────────────
echo ""
echo -e "${YELLOW}[2/5] Checking Sock Shop pods...${NC}"

DEPLOY_MANIFEST="$(dirname "$0")/target/app/deploy/kubernetes/complete-demo.yaml"

if ! kubectl get namespace sock-shop &>/dev/null; then
    info "sock-shop namespace not found — deploying Sock Shop..."
    kubectl apply -f "$DEPLOY_MANIFEST"
    info "Waiting for front-end pod to be created (this takes ~3 min on first deploy)..."
    # Poll until pod objects exist (may take 15-30s for scheduler to create them)
    local_elapsed=0
    until kubectl get pod -l name=front-end -n sock-shop --no-headers 2>/dev/null | grep -q .; do
        sleep 3; local_elapsed=$((local_elapsed+3))
        [ $local_elapsed -ge 180 ] && { warn "front-end pod not created after 3min"; break; }
    done
    kubectl wait --for=condition=ready pod -l name=front-end -n sock-shop --timeout=300s || \
        warn "front-end not ready within 5min — continuing anyway"
fi

RUNNING=$(kubectl get pods -n sock-shop --no-headers 2>/dev/null | grep -c "Running" || true)
TOTAL=$(kubectl get pods -n sock-shop --no-headers 2>/dev/null | wc -l || echo 0)

if [ "$RUNNING" -lt 8 ]; then
    info "Only $RUNNING/$TOTAL pods running — restarting deployments..."
    kubectl rollout restart deployment -n sock-shop
    info "Waiting for front-end pod..."
    kubectl wait --for=condition=ready pod -l name=front-end -n sock-shop --timeout=180s
    RUNNING=$(kubectl get pods -n sock-shop --no-headers 2>/dev/null | grep -c "Running" || true)
    ok "$RUNNING pods running"
else
    ok "$RUNNING/$TOTAL pods running"
fi

# ── Step 3: kubectl proxy ─────────────────────────────────────────────────────
echo ""
echo -e "${YELLOW}[3/5] Starting kubectl proxy...${NC}"
pkill -f "kubectl proxy --port=8001" 2>/dev/null || true
sleep 1
kubectl proxy --port=8001 > /tmp/kubectl-proxy.log 2>&1 &
PROXY_PID=$!
disown $PROXY_PID
if wait_for http://localhost:8001/api/v1/namespaces "kubectl proxy" 30; then
    ok "kubectl proxy running (PID $PROXY_PID) → http://localhost:8001"
else
    die "kubectl proxy failed to start — check /tmp/kubectl-proxy.log"
fi

# ── Step 4: Prometheus ───────────────────────────────────────────────────────
echo ""
echo -e "${YELLOW}[4/5] Starting Prometheus...${NC}"
pkill -f "port-forward.*9090" 2>/dev/null || true

# Prefer NodePort (no port-forward = no 'no relationship found' bug)
PROM_NODEPORT=$(kubectl get svc prometheus -n monitoring -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || true)
if [ -n "$PROM_NODEPORT" ] && curl -sf "http://${MINIKUBE_IP}:${PROM_NODEPORT}/-/healthy" > /dev/null 2>&1; then
    PROMETHEUS_URL="http://${MINIKUBE_IP}:${PROM_NODEPORT}"
    ok "Prometheus via NodePort → $PROMETHEUS_URL"
else
    # Fallback: port-forward to pod directly (avoids svc routing bug in kubectl 1.25+)
    PROM_POD=$(kubectl get pod -n monitoring -l app=prometheus --no-headers -o name 2>/dev/null | head -1 || true)
    if [ -z "$PROM_POD" ]; then
        die "No Prometheus pod found — is the monitoring stack deployed?"
    fi
    info "NodePort not available, using port-forward to $PROM_POD"
    kubectl port-forward -n monitoring "$PROM_POD" 9090:9090 --address=127.0.0.1 \
        > /tmp/prom-portforward.log 2>&1 &
    PROM_PID=$!
    disown $PROM_PID
    if wait_for http://localhost:9090/-/healthy "Prometheus" 45; then
        PROMETHEUS_URL="http://localhost:9090"
        ok "Prometheus reachable (PID $PROM_PID) → $PROMETHEUS_URL"
    else
        die "Prometheus unreachable — check /tmp/prom-portforward.log"
    fi
fi

# ── Step 5: Attack frontend ───────────────────────────────────────────────────
echo ""
echo -e "${YELLOW}[5/5] Starting KubeDDoS attack frontend (port 5001)...${NC}"
pkill -f "kubeddos-attacks/frontend/app.py" 2>/dev/null || true
sleep 1

if [ ! -d "$VENV" ]; then
    info "Creating Python virtual environment..."
    python3 -m venv "$VENV" || die "Failed to create venv — is python3-venv installed?"
fi

source "$VENV/bin/activate" || die "Failed to activate venv at $VENV"

# Always ensure dependencies are installed (fast no-op if already up to date)
info "Checking Python dependencies..."
pip install --quiet --upgrade pip
pip install --quiet -r "$PROJECT_ROOT/kubeddos-attacks/requirements.txt" \
    || die "Failed to install dependencies — check kubeddos-attacks/requirements.txt"

TARGET_URL="$TARGET_URL" \
ATTACKS_DIR="$PROJECT_ROOT/attacks" \
CONFIGS_DIR="$PROJECT_ROOT/attacks/configs" \
PROMETHEUS_URL="${PROMETHEUS_URL:-http://localhost:9090}" \
KUBERNETES_API_URL="http://localhost:8001" \
PYTHONPATH="$PROJECT_ROOT/kubeddos-attacks" \
python3 "$PROJECT_ROOT/kubeddos-attacks/frontend/app.py" > /tmp/kubeddos-attacks.log 2>&1 &
FRONTEND_PID=$!
disown $FRONTEND_PID 2>/dev/null || true
wait_status=0
wait_for http://localhost:5001/api/health "attack frontend" 30 "$FRONTEND_PID" || wait_status=$?
if [ "$wait_status" -eq 0 ]; then
    ok "Attack frontend running (PID $FRONTEND_PID) → http://localhost:5001"
else
    echo -e "${RED}  ✗ Frontend failed to start${NC}"
    echo ""
    echo -e "${YELLOW}  Last lines of /tmp/kubeddos-attacks.log:${NC}"
    tail -20 /tmp/kubeddos-attacks.log | sed 's/^/    /'
    echo ""
    die "Fix the error above, then re-run ./start.sh"
fi

# ── Ready ─────────────────────────────────────────────────────────────────────

# Resolve the machine's LAN/external IP (first non-loopback address)
HOST_IP=$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}' | head -1)
HOST_IP="${HOST_IP:-localhost}"

# Open firewall port 5001 if ufw is active
if command -v ufw &>/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
    if ! ufw status | grep -q "5001"; then
        info "Opening firewall port 5001 (ufw)..."
        ufw allow 5001/tcp > /dev/null
        ok "Firewall: port 5001 allowed"
    fi
fi

echo ""
echo -e "${GREEN}══════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  All services started successfully!${NC}"
echo -e "${GREEN}══════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  ${CYAN}Experiment Runner${NC}"
echo -e "    Local   →  http://localhost:5001/experiment"
echo -e "    Remote  →  http://${HOST_IP}:5001/experiment"
echo ""
echo -e "  ${CYAN}Attack Dashboard${NC}"
echo -e "    Local   →  http://localhost:5001"
echo -e "    Remote  →  http://${HOST_IP}:5001"
echo ""
echo -e "  ${CYAN}Grafana${NC}            →  http://${MINIKUBE_IP}:31300  (admin/admin)"
echo -e "  ${CYAN}Target App${NC}         →  $TARGET_URL"
echo ""
echo -e "  If you still can't reach the frontend from another machine:"
echo -e "    sudo ufw allow 5001/tcp   # if ufw is active"
echo -e "    sudo iptables -I INPUT -p tcp --dport 5001 -j ACCEPT  # if iptables"
echo ""
echo -e "  Log files:"
echo -e "    /tmp/kubectl-proxy.log"
echo -e "    /tmp/prom-portforward.log"
echo -e "    /tmp/kubeddos-attacks.log"
echo ""
echo -e "  To stop all services:"
echo -e "    pkill -f 'kubectl proxy'; pkill -f 'port-forward.*9090'; pkill -f 'kubeddos-attacks'"
echo ""

# Open the browser locally
BROWSER_URL="http://localhost:5001/experiment"
if command -v xdg-open &>/dev/null; then
    xdg-open "$BROWSER_URL" &>/dev/null &
elif command -v open &>/dev/null; then
    open "$BROWSER_URL" &>/dev/null &
else
    info "Open your browser at: $BROWSER_URL"
fi
