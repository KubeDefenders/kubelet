#!/bin/bash
# =============================================================================
# KubeDDoS — Resume Setup Script
#
# Run this if setup.sh failed at or after the Sock Shop deployment step
# (e.g. "target/app/deploy/kubernetes/complete-demo.yaml does not exist").
#
# Root cause: the `target/` git submodule was not initialised.
# This script fixes that then completes Steps 3-8 of setup.sh.
#
# Usage:
#   chmod +x setup-resume.sh && ./setup-resume.sh
# =============================================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BLUE='\033[0;34m'; NC='\033[0m'

log()  { echo -e "${CYAN}[$(date +%H:%M:%S)]${NC} $*"; }
ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC}  $*"; }
die()  { echo -e "${RED}✗ ERROR:${NC} $*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo -e "${BLUE}"
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║             KubeDDoS — Resume Setup (Steps 3-8)             ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# ── Prereq checks ─────────────────────────────────────────────────────────────
for cmd in kubectl minikube docker python3 git jq curl; do
    command -v "$cmd" &>/dev/null || die "'$cmd' not found. Run setup.sh first to install dependencies."
done

if ! docker info &>/dev/null; then
    die "Docker daemon not accessible. Try: sudo systemctl start docker && newgrp docker"
fi

# ── Helper ────────────────────────────────────────────────────────────────────
wait_for_pods() {
    local namespace="$1" timeout="${2:-300}"
    log "Waiting for pods in '$namespace' (up to ${timeout}s)..."
    kubectl wait --for=condition=Ready pod --all -n "$namespace" \
        --timeout="${timeout}s" 2>/dev/null || true
    local not_ready
    not_ready=$(kubectl get pods -n "$namespace" --no-headers 2>/dev/null \
        | grep -v " Running \| Completed " | grep -v "^$" || true)
    [[ -n "$not_ready" ]] && warn "Some pods may still be starting:\n$not_ready" || true
}

# =============================================================================
# FIX — Initialise the target/ git submodule
# =============================================================================
log "[Fix] Initialising git submodule (target/)..."

if [[ ! -f "target/app/deploy/kubernetes/complete-demo.yaml" ]]; then
    git submodule update --init --recursive
    ok "Submodule initialised"
else
    ok "Submodule already populated — skipping"
fi

[[ -f "target/app/deploy/kubernetes/complete-demo.yaml" ]] \
    || die "complete-demo.yaml still missing after submodule init. Check SSH key access to github.com/KubeDefenders/kube-target"

# =============================================================================
# STEP 2 — Ensure Minikube is running
# =============================================================================
log "[2/8] Checking Minikube..."

if ! minikube status 2>/dev/null | grep -q "Running"; then
    log "Starting Minikube..."
    minikube start \
        --driver=docker \
        --kubernetes-version=stable \
        --extra-config=kubelet.max-pods=250
    minikube addons enable metrics-server
    ok "Minikube started"
else
    ok "Minikube already running"
fi

MINIKUBE_IP=$(minikube ip)
ok "Cluster IP: ${MINIKUBE_IP}"

# =============================================================================
# STEP 3 — Deploy Sock Shop
# =============================================================================
log "[3/8] Deploying Sock Shop..."

kubectl create namespace sock-shop --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f target/app/deploy/kubernetes/complete-demo.yaml

log "Waiting for Sock Shop pods (this can take 3-5 min)..."
wait_for_pods sock-shop 360
ok "Sock Shop deployed → http://${MINIKUBE_IP}:30001"

# =============================================================================
# STEP 4 — Deploy monitoring stack
# =============================================================================
log "[4/8] Deploying monitoring stack..."

MONITORING_MANIFESTS="target/app/deploy/kubernetes/manifests-monitoring"

kubectl apply -f "${MONITORING_MANIFESTS}/00-monitoring-ns.yaml"
kubectl apply -f "${MONITORING_MANIFESTS}/01-prometheus-sa.yaml"
kubectl apply -f "${MONITORING_MANIFESTS}/02-prometheus-cr.yaml"
kubectl apply -f "${MONITORING_MANIFESTS}/03-prometheus-crb.yaml"
kubectl apply -f "${MONITORING_MANIFESTS}/04-prometheus-configmap.yaml"
kubectl apply -f "${MONITORING_MANIFESTS}/05-prometheus-alertrules.yaml"
kubectl apply -f "${MONITORING_MANIFESTS}/06-prometheus-dep.yaml"
kubectl apply -f "${MONITORING_MANIFESTS}/07-prometheus-svc.yaml"
kubectl apply -f "${MONITORING_MANIFESTS}/10-kube-state-sa.yaml"
kubectl apply -f "${MONITORING_MANIFESTS}/11-kube-state-cr.yaml"
kubectl apply -f "${MONITORING_MANIFESTS}/12-kube-state-crb.yaml"
kubectl apply -f "${MONITORING_MANIFESTS}/13-kube-state-dep.yaml"
kubectl apply -f "${MONITORING_MANIFESTS}/14-kube-state-svc.yaml"
kubectl apply -f "${MONITORING_MANIFESTS}/20-grafana-configmap.yaml"
kubectl apply -f "${MONITORING_MANIFESTS}/21-grafana-dep.yaml"
kubectl apply -f "${MONITORING_MANIFESTS}/22-grafana-svc.yaml"
kubectl apply -f "${MONITORING_MANIFESTS}/24-prometheus-node-exporter-sa.yaml"
kubectl apply -f "${MONITORING_MANIFESTS}/25-prometheus-node-exporter-daemonset.yaml"
kubectl apply -f "${MONITORING_MANIFESTS}/26-prometheus-node-exporter-svc.yaml"

log "Waiting for monitoring pods..."
wait_for_pods monitoring 240

PROM_CLUSTER_IP=$(kubectl get svc prometheus -n monitoring -o jsonpath='{.spec.clusterIP}')
ok "Prometheus → NodePort http://${MINIKUBE_IP}:31090 | ClusterIP ${PROM_CLUSTER_IP}:9090"
ok "Grafana    → http://${MINIKUBE_IP}:31300 (admin/admin)"

# =============================================================================
# STEP 5 — Provision Grafana datasource and dashboards
# =============================================================================
log "[5/8] Provisioning Grafana datasource and dashboards..."

GRAFANA_URL="http://${MINIKUBE_IP}:31300"
GRAFANA_AUTH="admin:admin"

log "Waiting for Grafana HTTP endpoint..."
for i in $(seq 1 30); do
    if curl -sf "${GRAFANA_URL}/api/health" &>/dev/null; then break; fi
    sleep 5
    [[ $i -eq 30 ]] && die "Grafana did not become ready. Check: kubectl get pods -n monitoring"
done
ok "Grafana is up"

# Datasource — use ClusterIP so the Grafana pod can reach Prometheus in-cluster
curl -sf -X POST "${GRAFANA_URL}/api/datasources" \
    -u "${GRAFANA_AUTH}" \
    -H "Content-Type: application/json" \
    -d "{
        \"name\": \"Prometheus\",
        \"type\": \"prometheus\",
        \"url\":  \"http://${PROM_CLUSTER_IP}:9090\",
        \"access\": \"proxy\",
        \"isDefault\": true
    }" &>/dev/null || true   # 409 = already exists, fine
ok "Grafana datasource configured"

import_dashboard() {
    local title="$1" json_file="$2"
    [[ -f "$json_file" ]] || { warn "Dashboard file missing, skipping: $json_file"; return; }
    local payload
    payload=$(jq -n \
        --argjson dash "$(cat "$json_file")" \
        '{
            dashboard: $dash,
            overwrite: true,
            inputs: [{
                name: "DS_PROMETHEUS",
                type: "datasource",
                pluginId: "prometheus",
                value: "Prometheus"
            }]
        }')
    curl -sf -X POST "${GRAFANA_URL}/api/dashboards/import" \
        -u "${GRAFANA_AUTH}" \
        -H "Content-Type: application/json" \
        -d "$payload" &>/dev/null \
        && ok "Dashboard: ${title}" \
        || warn "Failed to import dashboard: ${title}"
}

DASH_DIR="detection/monitoring"
import_dashboard "Sock Shop Performance"  "${DASH_DIR}/sock-shop-performance.json"
import_dashboard "Sock Shop Resources"    "${DASH_DIR}/sock-shop-resources.json"
import_dashboard "Sock Shop Analytics"    "${DASH_DIR}/sock-shop-analytics.json"
import_dashboard "K8s Pod Resources"      "${DASH_DIR}/kubernetes-pod-resources.json"
import_dashboard "K8s Node Resources"     "${DASH_DIR}/kubernetes-node-resources.json"

# =============================================================================
# STEP 6 — Python virtual environment
# =============================================================================
log "[6/8] Setting up Python virtual environment..."

[[ -d ".venv" ]] || python3 -m venv .venv
source .venv/bin/activate

pip install --quiet --upgrade pip
pip install --quiet -r kubeddos/requirements.txt
pip install --quiet -r kubeddos-attacks/requirements.txt
pip install --quiet -r detection/ml-detector/requirements.txt
# Downgrade eventlet — 0.41.0 crashes on import (missing OpenSSL symbol)
pip install --quiet "eventlet<0.36.0" 2>/dev/null || true

ok "Python environment ready ($(python3 --version))"

# =============================================================================
# STEP 7 — kubectl proxy
# =============================================================================
log "[7/8] Starting kubectl proxy on port 8001..."

pkill -f "kubectl proxy --port=8001" 2>/dev/null || true
sleep 1
kubectl proxy --port=8001 &>/tmp/kubectl-proxy.log &
PROXY_PID=$!
sleep 2
kill -0 "$PROXY_PID" 2>/dev/null \
    && ok "kubectl proxy running (PID $PROXY_PID)" \
    || die "kubectl proxy failed. Check /tmp/kubectl-proxy.log"

# =============================================================================
# STEP 8 — Start KubeDDoS frontends
# =============================================================================
log "[8/8] Starting KubeDDoS frontends..."

pkill -f "kubeddos/frontend/app.py" 2>/dev/null || true
pkill -f "kubeddos-attacks/frontend/app.py" 2>/dev/null || true
pkill -f "kubectl port-forward.*prometheus" 2>/dev/null || true
sleep 1

# Prometheus port-forward → localhost:9090 (used by frontends and scripts)
kubectl port-forward -n monitoring svc/prometheus 9090:9090 \
    &>/tmp/prom-portforward.log &
PROM_PF_PID=$!
sleep 2
ok "Prometheus port-forward running (PID $PROM_PF_PID) → localhost:9090"

source .venv/bin/activate

# System dashboard (port 5000)
KUBERNETES_API_URL=http://localhost:8001 \
K8S_TOKEN=unused \
PROMETHEUS_URL=http://localhost:9090 \
PYTHONPATH="${SCRIPT_DIR}" \
    python3 kubeddos/frontend/app.py &>/tmp/kubeddos-frontend.log &
FE1_PID=$!
sleep 2
kill -0 "$FE1_PID" 2>/dev/null \
    && ok "KubeDDoS system dashboard (PID $FE1_PID) → http://localhost:5000" \
    || warn "System dashboard failed. Check /tmp/kubeddos-frontend.log"

# Attack frontend (port 5001)
KUBERNETES_API_URL=http://localhost:8001 \
K8S_TOKEN=unused \
PROMETHEUS_URL=http://localhost:9090 \
PYTHONPATH="${SCRIPT_DIR}" \
    python3 kubeddos-attacks/frontend/app.py &>/tmp/kubeddos-attacks.log &
FE2_PID=$!
sleep 2
kill -0 "$FE2_PID" 2>/dev/null \
    && ok "KubeDDoS attack frontend (PID $FE2_PID) → http://localhost:5001" \
    || warn "Attack frontend failed. Check /tmp/kubeddos-attacks.log"

# =============================================================================
# Summary
# =============================================================================
echo ""
echo -e "${BLUE}╔══════════════════════════════════════════════════════════════╗"
echo -e "║                    Setup Complete!                          ║"
echo -e "╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${GREEN}Target Application${NC}"
echo    "    Sock Shop UI       →  http://${MINIKUBE_IP}:30001"
echo ""
echo -e "  ${GREEN}Monitoring${NC}"
echo    "    Prometheus         →  http://${MINIKUBE_IP}:31090"
echo    "                          http://localhost:9090 (port-forward)"
echo    "    Grafana            →  http://${MINIKUBE_IP}:31300"
echo    "    Grafana login      →  admin / admin"
echo ""
echo -e "  ${GREEN}KubeDDoS Frontends${NC}"
echo    "    System dashboard   →  http://localhost:5000"
echo    "    Attack frontend    →  http://localhost:5001"
echo ""
echo -e "  ${GREEN}Run an experiment${NC}"
echo    "    ATTACK_DURATION=300 ATTACK_WORKERS=100 ATTACK_RATE=30 \\"
echo    "        ./scripts/workflows/quick-mitigation-comparison.sh"
echo ""
echo -e "  ${GREEN}Log files${NC}"
echo    "    /tmp/kubectl-proxy.log"
echo    "    /tmp/prom-portforward.log"
echo    "    /tmp/kubeddos-frontend.log"
echo    "    /tmp/kubeddos-attacks.log"
echo ""
echo -e "  ${YELLOW}To stop everything:${NC}"
echo    "    pkill -f 'kubectl proxy\|kubectl port-forward\|kubeddos.*app.py'"
echo ""
