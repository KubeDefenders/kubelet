#!/usr/bin/env bash
# =============================================================================
# setup-nephio-deployment.sh
#
# Sets up the complete KubeDDoS environment from scratch and deploys the
# DDoS mitigation software via the Nephio package management workflow:
#
#   1. Start (or verify) a Minikube cluster
#   2. Deploy Sock Shop target application
#   3. Build the kubeddos-controller container image (minikube Docker daemon)
#   4. Apply nephio-package/kubeddos-system  → namespace, CRDs, RBAC, controller
#   5. Wait for the controller pod to become Ready
#   6. Apply nephio-package/kubeddos-protection → DDoSProtection intent CR
#   7. Wait for controller to reconcile (HPAs + NetworkPolicies)
#   8. Pre-scale front-end to HPA minReplicas so capacity is ready before attack
#
# Usage:
#   bash scripts/setup-nephio-deployment.sh [--clean]
#
#   --clean   delete and recreate the Minikube cluster first
#
# Prerequisites:
#   minikube, kubectl, docker  (auto-installed by scripts/cluster/setup-minikube.sh
#   if missing)
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Colours
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

step()  { echo -e "\n${CYAN}${BOLD}[$(date +%H:%M:%S)] $*${NC}"; }
ok()    { echo -e "${GREEN}  ✓ $*${NC}"; }
warn()  { echo -e "${YELLOW}  ! $*${NC}"; }
die()   { echo -e "${RED}  ✗ $*${NC}"; exit 1; }

CLEAN_CLUSTER=false
for arg in "$@"; do
    [[ "$arg" == "--clean" ]] && CLEAN_CLUSTER=true
done

echo -e "${BOLD}"
echo "  ╔══════════════════════════════════════════════════════════╗"
echo "  ║     KubeDDoS · Nephio Package Deployment Setup          ║"
echo "  ╚══════════════════════════════════════════════════════════╝"
echo -e "${NC}"

cd "$REPO_ROOT"

# ─── 0. Prerequisites ────────────────────────────────────────────────────────
step "0/8  Checking prerequisites"

for cmd in minikube kubectl docker; do
    if ! command -v "$cmd" &>/dev/null; then
        die "$cmd not found. Run scripts/cluster/setup-minikube.sh first."
    fi
    ok "$cmd found: $(command -v $cmd)"
done

# ─── 1. Minikube ─────────────────────────────────────────────────────────────
step "1/8  Minikube cluster"

if $CLEAN_CLUSTER; then
    warn "Deleting existing Minikube cluster (--clean flag)…"
    minikube delete 2>/dev/null || true
fi

if minikube status 2>/dev/null | grep -q "Running"; then
    ok "Minikube already running"
else
    warn "Starting Minikube…"
    minikube start \
        --driver=docker \
        --memory=4096 \
        --cpus=4 \
        --kubernetes-version=stable \
        --extra-config=kubelet.max-pods=250
    ok "Minikube started"
fi

# Enable addons needed for HPA to work
minikube addons enable metrics-server 2>/dev/null | grep -v "^$" || true

MINIKUBE_IP=$(minikube ip)
ok "Cluster IP: $MINIKUBE_IP"

# ─── 2. Sock Shop ─────────────────────────────────────────────────────────────
step "2/8  Deploying Sock Shop"

kubectl create namespace sock-shop --dry-run=client -o yaml | kubectl apply -f -

SOCK_SHOP_MANIFEST="target/app/deploy/kubernetes/complete-demo.yaml"
if [[ ! -f "$SOCK_SHOP_MANIFEST" ]]; then
    die "Sock Shop manifest not found at $SOCK_SHOP_MANIFEST. Is the submodule checked out?"
fi

kubectl apply -f "$SOCK_SHOP_MANIFEST"
ok "Sock Shop manifests applied"

step "  Waiting for Sock Shop pods (up to 5 min)…"
WAIT_SECS=0
while [ $WAIT_SECS -lt 300 ]; do
    NOT_READY=$(kubectl get pods -n sock-shop --no-headers 2>/dev/null \
        | grep -v Running | grep -v Completed | wc -l)
    TOTAL=$(kubectl get pods -n sock-shop --no-headers 2>/dev/null | wc -l)
    if [ "$NOT_READY" -eq 0 ] && [ "$TOTAL" -gt 0 ]; then
        ok "All $TOTAL Sock Shop pods Running"
        break
    fi
    echo -e "  ${YELLOW}[${WAIT_SECS}s] $((TOTAL - NOT_READY))/$TOTAL pods Running…${NC}"
    sleep 10
    WAIT_SECS=$((WAIT_SECS + 10))
done

if [ $WAIT_SECS -ge 300 ]; then
    warn "Some pods not yet Running after 5 min — continuing anyway"
    kubectl get pods -n sock-shop --no-headers | grep -v Running || true
fi

# ─── 3. Build controller image ────────────────────────────────────────────────
step "3/8  Building kubeddos-controller image (minikube Docker daemon)"

# Build directly into Minikube so no registry is needed
eval "$(minikube docker-env)"

docker build \
    --tag kubeddos-controller:latest \
    --file nephio-package/kubeddos-system/Dockerfile \
    mitigation/nephio/controller/

# Verify image is present in the cluster's image store
if docker images | grep -q "kubeddos-controller"; then
    ok "Image kubeddos-controller:latest built and available in Minikube"
else
    die "Image build appeared to succeed but image not found"
fi

# Restore host Docker env
eval "$(minikube docker-env --unset)"

# ─── 4. Apply kubeddos-system package ─────────────────────────────────────────
step "4/8  Applying kubeddos-system package (CRDs, RBAC, controller Deployment)"

# Clean up any previous kubeddos installation
kubectl delete namespace crossfire-system --ignore-not-found=true 2>/dev/null || true
# Brief pause to let namespace finalizer clear
sleep 3

kubectl apply -f nephio-package/kubeddos-system/00-namespace.yaml
kubectl apply -f nephio-package/kubeddos-system/01-crds.yaml
kubectl apply -f nephio-package/kubeddos-system/02-rbac.yaml
kubectl apply -f nephio-package/kubeddos-system/03-controller.yaml

ok "kubeddos-system manifests applied"

# ─── 5. Wait for controller pod ───────────────────────────────────────────────
step "5/8  Waiting for kubeddos-controller pod to be Ready (up to 2 min)"

WAIT_SECS=0
CONTROLLER_READY=false
while [ $WAIT_SECS -lt 120 ]; do
    PHASE=$(kubectl get pods -n crossfire-system -l app.kubernetes.io/name=kubeddos-controller \
        --no-headers 2>/dev/null | awk '{print $3}' | head -1)
    if [[ "$PHASE" == "Running" ]]; then
        READY=$(kubectl get pods -n crossfire-system -l app.kubernetes.io/name=kubeddos-controller \
            --no-headers 2>/dev/null | awk '{print $2}' | head -1)
        if [[ "$READY" == "1/1" ]]; then
            CONTROLLER_READY=true
            break
        fi
    fi
    echo -e "  ${YELLOW}[${WAIT_SECS}s] controller pod: ${PHASE:-Pending}…${NC}"
    sleep 5
    WAIT_SECS=$((WAIT_SECS + 5))
done

if ! $CONTROLLER_READY; then
    warn "Controller pod not fully Ready — checking logs:"
    kubectl logs -n crossfire-system \
        -l app.kubernetes.io/name=kubeddos-controller --tail=20 2>/dev/null || true
    die "Controller pod failed to start. Aborting."
fi

CONTROLLER_POD=$(kubectl get pods -n crossfire-system \
    -l app.kubernetes.io/name=kubeddos-controller -o name | head -1)
ok "Controller pod Ready: $CONTROLLER_POD"

# ─── 6. Apply kubeddos-protection package ────────────────────────────────────
step "6/8  Applying kubeddos-protection package (DDoSProtection intent CR)"

# Remove any existing DDoSProtection CRs (force-clear kopf finalizer first)
for cr in $(kubectl get ddosprotection -n sock-shop -o name 2>/dev/null); do
    kubectl patch "$cr" -n sock-shop --type=json \
        -p='[{"op":"remove","path":"/metadata/finalizers"}]' 2>/dev/null || true
done
kubectl delete ddosprotection --all -n sock-shop --ignore-not-found=true 2>/dev/null || true

# Wait for CRs to be fully gone
CR_WAIT=0
while [ $CR_WAIT -lt 20 ]; do
    COUNT=$(kubectl get ddosprotection -n sock-shop --no-headers 2>/dev/null | wc -l)
    [ "$COUNT" -eq 0 ] && break
    sleep 2
    CR_WAIT=$((CR_WAIT + 2))
done

kubectl apply -f nephio-package/kubeddos-protection/ddos-protection.yaml
ok "DDoSProtection CR applied"

# ─── 7. Wait for reconciliation ───────────────────────────────────────────────
step "7/8  Waiting for controller to reconcile (HPAs + NetworkPolicies)"

WAIT_SECS=0
RECONCILED=false
while [ $WAIT_SECS -lt 120 ]; do
    HPA_COUNT=$(kubectl get hpa -n sock-shop --no-headers 2>/dev/null \
        | grep "nephio-hpa" | wc -l)
    NETPOL_COUNT=$(kubectl get networkpolicies -n sock-shop --no-headers 2>/dev/null \
        | grep "nephio-" | wc -l)
    if [ "$HPA_COUNT" -ge 1 ] && [ "$NETPOL_COUNT" -ge 1 ]; then
        RECONCILED=true
        break
    fi
    echo -e "  ${YELLOW}[${WAIT_SECS}s] reconciling… HPAs=${HPA_COUNT} NetPols=${NETPOL_COUNT}${NC}"
    sleep 5
    WAIT_SECS=$((WAIT_SECS + 5))
done

if ! $RECONCILED; then
    warn "Controller logs (last 30 lines):"
    kubectl logs -n crossfire-system \
        -l app.kubernetes.io/name=kubeddos-controller --tail=30 2>/dev/null || true
    die "Controller did not reconcile resources within 2 min. See logs above."
fi

HPA_COUNT=$(kubectl get hpa -n sock-shop --no-headers 2>/dev/null | grep "nephio-hpa" | wc -l)
NETPOL_COUNT=$(kubectl get networkpolicies -n sock-shop --no-headers 2>/dev/null | grep "nephio-" | wc -l)
ok "Reconciled: ${HPA_COUNT} HPAs, ${NETPOL_COUNT} NetworkPolicies"

# Show generated resources
echo ""
kubectl get hpa -n sock-shop 2>/dev/null | grep -E "NAME|nephio" || true
echo ""
kubectl get networkpolicies -n sock-shop 2>/dev/null | grep -E "NAME|nephio" || true

# ─── 8. Pre-scale front-end ───────────────────────────────────────────────────
step "8/8  Pre-scaling front-end to HPA minReplicas (high level = 3)"

# The HPA will eventually hold this at minReplicas=3; we do it explicitly so
# capacity is available immediately without waiting for an HPA reconcile cycle.
kubectl scale deployment front-end --replicas=3 -n sock-shop
echo -e "  Waiting for front-end rollout…"
kubectl rollout status deployment/front-end -n sock-shop --timeout=120s
ok "front-end rolled out at 3 replicas"

# ─── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}"
echo "  ╔══════════════════════════════════════════════════════════╗"
echo "  ║              Setup Complete                             ║"
echo "  ╚══════════════════════════════════════════════════════════╝"
echo -e "${NC}"

FRONTEND_PORT=$(kubectl get svc front-end -n sock-shop \
    -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || echo "30001")

echo -e "  ${BOLD}Cluster IP :${NC}  $MINIKUBE_IP"
echo -e "  ${BOLD}Sock Shop  :${NC}  http://$MINIKUBE_IP:$FRONTEND_PORT"
echo -e "  ${BOLD}Protection :${NC}  kubeddos-controller (crossfire-system namespace)"
echo ""
echo -e "  ${BOLD}Resources generated by the Nephio controller:${NC}"
kubectl get hpa,networkpolicies -n sock-shop --no-headers 2>/dev/null \
    | grep "nephio-" | awk '{printf "    %-50s %s\n", $1, $2}' || true
echo ""
echo -e "  ${BOLD}Pods:${NC}"
kubectl get pods -n sock-shop --no-headers 2>/dev/null | awk '{printf "    %-40s %s\n", $1, $3}'
echo ""
echo -e "  ${BOLD}Next steps:${NC}"
echo "    Run experiment:  bash scripts/workflows/quick-mitigation-comparison.sh"
echo "    Watch HPAs:      kubectl get hpa -n sock-shop -w"
echo "    Controller logs: kubectl logs -n crossfire-system -l app.kubernetes.io/name=kubeddos-controller -f"
