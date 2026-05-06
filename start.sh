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
# Polls URL every second until it responds or timeout is reached.
wait_for() {
    local url="$1" label="$2" timeout="${3:-30}"
    local elapsed=0
    printf "  ${CYAN}→ Waiting for %s" "$label"
    while ! curl -sf "$url" > /dev/null 2>&1; do
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

MINIKUBE_IP=$(minikube ip)
NODE_PORT=$(kubectl get svc -n sock-shop front-end -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || echo "30001")
TARGET_URL="http://${MINIKUBE_IP}:${NODE_PORT}"

# ── Step 2: Sock Shop pods ────────────────────────────────────────────────────
echo ""
echo -e "${YELLOW}[2/5] Checking Sock Shop pods...${NC}"

RUNNING=$(kubectl get pods -n sock-shop --no-headers 2>/dev/null | grep -c "Running" || true)
TOTAL=$(kubectl get pods -n sock-shop --no-headers 2>/dev/null | wc -l || echo 0)

if [ "$RUNNING" -lt 8 ]; then
    info "Only $RUNNING/$TOTAL pods running — restarting deployments..."
    kubectl rollout restart deployment -n sock-shop
    info "Waiting for front-end pod..."
    kubectl wait --for=condition=ready pod -l name=front-end -n sock-shop --timeout=120s
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
    die "Python venv not found at $VENV — run setup.sh first"
fi

source "$VENV/bin/activate"

# Check required packages are installed; auto-install if missing
MISSING=$(python3 -c "
import importlib, sys
pkgs = {'flask':'flask','flask_socketio':'flask-socketio','yaml':'pyyaml',
        'requests':'requests','aiohttp':'aiohttp','bs4':'beautifulsoup4','locust':'locust'}
missing = [pip for mod,pip in pkgs.items() if importlib.util.find_spec(mod) is None]
print(' '.join(missing))
" 2>/dev/null)
if [ -n "$MISSING" ]; then
    info "Installing missing Python packages: $MISSING"
    pip install --quiet $MISSING
fi

TARGET_URL="$TARGET_URL" \
ATTACKS_DIR="$PROJECT_ROOT/attacks" \
CONFIGS_DIR="$PROJECT_ROOT/attacks/configs" \
PROMETHEUS_URL="${PROMETHEUS_URL:-http://localhost:9090}" \
KUBERNETES_API_URL="http://localhost:8001" \
PYTHONPATH="$PROJECT_ROOT/kubeddos-attacks" \
python3 "$PROJECT_ROOT/kubeddos-attacks/frontend/app.py" > /tmp/kubeddos-attacks.log 2>&1 &
FRONTEND_PID=$!
disown $FRONTEND_PID
if wait_for http://localhost:5001/api/health "attack frontend" 30; then
    ok "Attack frontend running (PID $FRONTEND_PID) → http://localhost:5001"
else
    die "Frontend failed to start — check /tmp/kubeddos-attacks.log"
fi

# ── Ready ─────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}══════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  All services started successfully!${NC}"
echo -e "${GREEN}══════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  ${CYAN}Experiment Runner${NC}  →  http://localhost:5001/experiment"
echo -e "  ${CYAN}Attack Dashboard${NC}   →  http://localhost:5001"
echo -e "  ${CYAN}Grafana${NC}            →  http://${MINIKUBE_IP}:31300  (admin/admin)"
echo -e "  ${CYAN}Target App${NC}         →  $TARGET_URL"
echo ""
echo -e "  Log files:"
echo -e "    /tmp/kubectl-proxy.log"
echo -e "    /tmp/prom-portforward.log"
echo -e "    /tmp/kubeddos-attacks.log"
echo ""
echo -e "  To stop all services:"
echo -e "    pkill -f 'kubectl proxy'; pkill -f 'port-forward.*9090'; pkill -f 'kubeddos-attacks'"
echo ""
