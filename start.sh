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
sleep 2
if curl -sf http://localhost:8001/api/v1/namespaces > /dev/null 2>&1; then
    ok "kubectl proxy running (PID $PROXY_PID) → http://localhost:8001"
else
    die "kubectl proxy failed to start — check /tmp/kubectl-proxy.log"
fi

# ── Step 4: Prometheus port-forward ──────────────────────────────────────────
echo ""
echo -e "${YELLOW}[4/5] Starting Prometheus port-forward...${NC}"
pkill -f "port-forward.*9090" 2>/dev/null || true
sleep 1
kubectl port-forward -n monitoring svc/prometheus 9090:9090 > /tmp/prom-portforward.log 2>&1 &
PROM_PID=$!
disown $PROM_PID
sleep 3
if curl -sf http://localhost:9090/-/healthy > /dev/null 2>&1; then
    ok "Prometheus reachable (PID $PROM_PID) → http://localhost:9090"
else
    warn "Prometheus port-forward may not be ready yet — continuing anyway"
    warn "Check /tmp/prom-portforward.log if metrics don't work"
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

TARGET_URL="$TARGET_URL" \
ATTACKS_DIR="$PROJECT_ROOT/attacks" \
CONFIGS_DIR="$PROJECT_ROOT/attacks/configs" \
PROMETHEUS_URL="http://localhost:9090" \
KUBERNETES_API_URL="http://localhost:8001" \
PYTHONPATH="$PROJECT_ROOT/kubeddos-attacks" \
python3 "$PROJECT_ROOT/kubeddos-attacks/frontend/app.py" > /tmp/kubeddos-attacks.log 2>&1 &
FRONTEND_PID=$!
disown $FRONTEND_PID
sleep 3

if curl -sf http://localhost:5001/api/health > /dev/null 2>&1; then
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
