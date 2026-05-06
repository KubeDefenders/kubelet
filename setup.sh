#!/bin/bash
# =============================================================================
# KubeDDoS — Full Setup Script
# Tested on Ubuntu 22.04 LTS (fresh installation)
#
# Usage:
#   chmod +x setup.sh && ./setup.sh
#
# What this does:
#   1. Installs system dependencies (Docker, kubectl, Minikube, Python 3.10, Helm)
#   2. Starts a Minikube cluster
#   3. Deploys Sock Shop target application
#   4. Deploys Prometheus + Grafana monitoring stack
#   5. Provisions Grafana datasource and dashboards
#   6. Sets up the Python virtual environment
#   7. Starts the KubeDDoS frontends (ports 5000 and 5001)
#   8. Starts kubectl proxy for K8s API access
#   9. Prints access URLs and next steps
# =============================================================================

set -euo pipefail

# ── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BLUE='\033[0;34m'
NC='\033[0m'

log()  { echo -e "${CYAN}[$(date +%H:%M:%S)]${NC} $*"; }
ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC}  $*"; }
die()  { echo -e "${RED}✗ ERROR:${NC} $*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo -e "${BLUE}"
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║                  KubeDDoS Setup Script                      ║"
echo "║          Sock Shop  ·  Prometheus  ·  Grafana                ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# ── Helper functions ─────────────────────────────────────────────────────────
command_exists() { command -v "$1" &>/dev/null; }

wait_for_pods() {
    local namespace="$1"
    local timeout="${2:-300}"
    log "Waiting for all pods in namespace '$namespace' (up to ${timeout}s)..."
    kubectl wait --for=condition=Ready pod --all -n "$namespace" \
        --timeout="${timeout}s" 2>/dev/null || true
    # Print any pods still not Ready
    local not_ready
    not_ready=$(kubectl get pods -n "$namespace" --no-headers 2>/dev/null \
        | grep -v " Running \| Completed " | grep -v "^$" || true)
    if [[ -n "$not_ready" ]]; then
        warn "Some pods may still be starting:\n$not_ready"
    fi
}

# =============================================================================
# STEP 1 — System dependencies
# =============================================================================
log "[1/8] Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y \
    curl wget git \
    apt-transport-https ca-certificates gnupg lsb-release \
    conntrack socat \
    python3.10 python3.10-venv python3-pip \
    jq 2>/dev/null
ok "System packages installed"

# Docker
if ! command_exists docker; then
    log "Installing Docker..."
    curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
    sudo sh /tmp/get-docker.sh
    rm /tmp/get-docker.sh
    sudo usermod -aG docker "$USER"
    ok "Docker installed — NOTE: you must log out and back in for the docker group to take effect"
    warn "If this is your first Docker install, run: newgrp docker"
    # Attempt to activate docker group in the current session
    if ! groups | grep -q docker; then
        warn "Re-exec with docker group for the rest of this script..."
        exec newgrp docker <<EONG
bash "$SCRIPT_DIR/setup.sh"
EONG
        exit 0
    fi
else
    ok "Docker already installed: $(docker --version)"
fi

# Ensure docker is accessible without sudo in current session
if ! docker info &>/dev/null; then
    die "Docker daemon is not accessible. Make sure you are in the 'docker' group and the daemon is running. Try: sudo systemctl start docker && newgrp docker"
fi

# kubectl
if ! command_exists kubectl; then
    log "Installing kubectl..."
    KUBECTL_VERSION=$(curl -fsSL https://dl.k8s.io/release/stable.txt)
    curl -fsSLo /tmp/kubectl \
        "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl"
    sudo install -o root -g root -m 0755 /tmp/kubectl /usr/local/bin/kubectl
    rm /tmp/kubectl
    ok "kubectl installed: $(kubectl version --client --short 2>/dev/null || kubectl version --client)"
else
    ok "kubectl already installed"
fi

# Minikube
if ! command_exists minikube; then
    log "Installing Minikube..."
    curl -fsSLo /tmp/minikube-linux-amd64 \
        https://storage.googleapis.com/minikube/releases/latest/minikube-linux-amd64
    sudo install /tmp/minikube-linux-amd64 /usr/local/bin/minikube
    rm /tmp/minikube-linux-amd64
    ok "Minikube installed: $(minikube version --short)"
else
    ok "Minikube already installed"
fi

# Helm (used for optional extensions)
if ! command_exists helm; then
    log "Installing Helm..."
    curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
    ok "Helm installed"
else
    ok "Helm already installed"
fi

# =============================================================================
# STEP 2 — Start Minikube
# =============================================================================
log "[2/8] Starting Minikube cluster..."

if minikube status 2>/dev/null | grep -q "Running"; then
    ok "Minikube already running ($(minikube ip))"
else
    minikube start \
        --driver=docker \
        --kubernetes-version=stable \
        --extra-config=kubelet.max-pods=250
    minikube addons enable metrics-server
    ok "Minikube started"
fi

MINIKUBE_IP=$(minikube ip)
ok "Cluster IP: ${MINIKUBE_IP}"

# =============================================================================
# STEP 3 — Deploy Sock Shop (target application)
# =============================================================================
log "[3/8] Deploying Sock Shop target application..."

# Ensure the target/ git submodule is populated (not pulled by default on clone)
if [[ ! -f "target/app/deploy/kubernetes/complete-demo.yaml" ]]; then
    log "Initialising git submodule (target/)..."
    git submodule update --init --recursive \
        || die "Failed to initialise target/ submodule. Ensure SSH access to github.com/KubeDefenders/kube-target (run: ssh -T git@github.com)"
    ok "Submodule initialised"
fi

kubectl create namespace sock-shop --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f target/app/deploy/kubernetes/complete-demo.yaml

log "Waiting for Sock Shop pods to become ready (this can take 3-5 min)..."
wait_for_pods sock-shop 360
ok "Sock Shop deployed → http://${MINIKUBE_IP}:30001"

# =============================================================================
# STEP 4 — Deploy monitoring stack (Prometheus + Grafana)
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

# Resolve Prometheus ClusterIP (used by Grafana datasource — must be in-cluster)
PROM_CLUSTER_IP=$(kubectl get svc prometheus -n monitoring -o jsonpath='{.spec.clusterIP}')
ok "Prometheus deployed → NodePort http://${MINIKUBE_IP}:31090 | ClusterIP ${PROM_CLUSTER_IP}:9090"
ok "Grafana deployed   → http://${MINIKUBE_IP}:31300 (admin/admin)"

# =============================================================================
# STEP 5 — Provision Grafana datasource and dashboards
# =============================================================================
log "[5/8] Provisioning Grafana datasource and dashboards..."

GRAFANA_URL="http://${MINIKUBE_IP}:31300"
GRAFANA_AUTH="admin:admin"

# Wait for Grafana HTTP endpoint
log "Waiting for Grafana to respond..."
for i in $(seq 1 30); do
    if curl -sf "${GRAFANA_URL}/api/health" &>/dev/null; then
        break
    fi
    sleep 5
    if [[ $i -eq 30 ]]; then
        die "Grafana did not become ready in time. Check: kubectl get pods -n monitoring"
    fi
done
ok "Grafana is up"

# Create Prometheus datasource (use ClusterIP so Grafana pod can reach it)
curl -sf -X POST "${GRAFANA_URL}/api/datasources" \
    -u "${GRAFANA_AUTH}" \
    -H "Content-Type: application/json" \
    -d "{
        \"name\": \"Prometheus\",
        \"type\": \"prometheus\",
        \"url\":  \"http://${PROM_CLUSTER_IP}:9090\",
        \"access\": \"proxy\",
        \"isDefault\": true
    }" &>/dev/null || true   # 409 = already exists, ignore
ok "Grafana datasource configured"

# Import dashboards — must use /api/dashboards/import with DS_PROMETHEUS input
# so the \${DS_PROMETHEUS} template variable is resolved (not left as literal)
import_dashboard() {
    local title="$1"
    local json_file="$2"
    if [[ ! -f "$json_file" ]]; then
        warn "Dashboard file not found, skipping: $json_file"
        return
    fi
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
        -d "$payload" &>/dev/null && ok "Dashboard imported: ${title}" \
        || warn "Failed to import dashboard: ${title}"
}

DASH_DIR="detection/monitoring"
import_dashboard "Sock Shop Performance"  "${DASH_DIR}/sock-shop-performance.json"
import_dashboard "Sock Shop Resources"    "${DASH_DIR}/sock-shop-resources.json"
import_dashboard "Sock Shop Analytics"   "${DASH_DIR}/sock-shop-analytics.json"
import_dashboard "K8s Pod Resources"     "${DASH_DIR}/kubernetes-pod-resources.json"
import_dashboard "K8s Node Resources"    "${DASH_DIR}/kubernetes-node-resources.json"

# =============================================================================
# STEP 6 — Python virtual environment
# =============================================================================
log "[6/8] Setting up Python virtual environment..."

if [[ ! -d ".venv" ]]; then
    python3.10 -m venv .venv
fi
source .venv/bin/activate

pip install --quiet --upgrade pip

# Install all component requirements
pip install --quiet -r kubeddos/requirements.txt
pip install --quiet -r kubeddos-attacks/requirements.txt
pip install --quiet -r detection/ml-detector/requirements.txt

ok "Python environment ready ($(python3 --version))"

# =============================================================================
# STEP 7 — Start kubectl proxy (needed by both frontends for K8s API)
# =============================================================================
log "[7/8] Starting kubectl proxy on port 8001..."

# Kill any existing proxy on port 8001
pkill -f "kubectl proxy --port=8001" 2>/dev/null || true
sleep 1

kubectl proxy --port=8001 &>/tmp/kubectl-proxy.log &
PROXY_PID=$!
sleep 2

if kill -0 "$PROXY_PID" 2>/dev/null; then
    ok "kubectl proxy running (PID $PROXY_PID)"
else
    die "kubectl proxy failed to start. Check /tmp/kubectl-proxy.log"
fi

# =============================================================================
# STEP 8 — Start KubeDDoS frontends
# =============================================================================
log "[8/8] Starting KubeDDoS frontends..."

export PYTHONPATH="${SCRIPT_DIR}"
export KUBERNETES_API_URL="http://localhost:8001"
export K8S_TOKEN="unused"
export PROMETHEUS_URL="http://localhost:9090"

# Kill any existing instances
pkill -f "kubeddos/frontend/app.py" 2>/dev/null || true
pkill -f "kubeddos-attacks/frontend/app.py" 2>/dev/null || true
sleep 1

# Port-forward Prometheus to localhost:9090 (required by frontends and scripts)
pkill -f "kubectl port-forward.*prometheus" 2>/dev/null || true
sleep 1
kubectl port-forward -n monitoring svc/prometheus 9090:9090 &>/tmp/prom-portforward.log &
PROM_PF_PID=$!
sleep 2
ok "Prometheus port-forward running (PID $PROM_PF_PID) → localhost:9090"

# KubeDDoS system dashboard (port 5000)
source .venv/bin/activate
KUBERNETES_API_URL=http://localhost:8001 \
K8S_TOKEN=unused \
PROMETHEUS_URL=http://localhost:9090 \
PYTHONPATH="${SCRIPT_DIR}" \
    python3 kubeddos/frontend/app.py &>/tmp/kubeddos-frontend.log &
FE1_PID=$!
sleep 2

if kill -0 "$FE1_PID" 2>/dev/null; then
    ok "KubeDDoS system dashboard running (PID $FE1_PID) → http://localhost:5000"
else
    warn "KubeDDoS system dashboard failed to start. Check /tmp/kubeddos-frontend.log"
fi

# KubeDDoS attack frontend (port 5001)
KUBERNETES_API_URL=http://localhost:8001 \
K8S_TOKEN=unused \
PROMETHEUS_URL=http://localhost:9090 \
PYTHONPATH="${SCRIPT_DIR}" \
    python3 kubeddos-attacks/frontend/app.py &>/tmp/kubeddos-attacks.log &
FE2_PID=$!
sleep 2

if kill -0 "$FE2_PID" 2>/dev/null; then
    ok "KubeDDoS attack frontend running (PID $FE2_PID) → http://localhost:5001"
else
    warn "KubeDDoS attack frontend failed to start. Check /tmp/kubeddos-attacks.log"
fi

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
echo    "                          http://localhost:3000 (if port-forwarded)"
echo    "    Grafana login      →  admin / admin"
echo ""
echo -e "  ${GREEN}KubeDDoS Frontends${NC}"
echo    "    System dashboard   →  http://localhost:5000"
echo    "    Attack frontend    →  http://localhost:5001"
echo ""
echo -e "  ${GREEN}Run an attack${NC}"
echo    "    cd ${SCRIPT_DIR}"
echo    "    source config/runtime.env"
echo    "    ATTACK_DURATION=300 ATTACK_WORKERS=100 ATTACK_RATE=30 \\"
echo    "        ./scripts/workflows/quick-mitigation-comparison.sh"
echo ""
echo -e "  ${GREEN}Log files${NC}"
echo    "    kubectl proxy      →  /tmp/kubectl-proxy.log"
echo    "    Prometheus pf      →  /tmp/prom-portforward.log"
echo    "    System frontend    →  /tmp/kubeddos-frontend.log"
echo    "    Attack frontend    →  /tmp/kubeddos-attacks.log"
echo ""
echo -e "  ${YELLOW}To stop everything:${NC}"
echo    "    pkill -f 'kubectl proxy\|kubectl port-forward\|kubeddos.*app.py'"
echo ""
