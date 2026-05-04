#!/bin/bash

# Reinstantiate Minikube and Setup Port Forwarding
# This script restarts Minikube, restarts all pods, and sets up port forwarding for monitoring and frontend

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Configuration (no limits by default - use all available resources)
MEMORY="${1:-}"
CPUS="${2:-}"

echo -e "${BLUE}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║                                                                ║${NC}"
echo -e "${BLUE}║       ${CYAN}Minikube Reinstantiation & Port Forward Setup${BLUE}          ║${NC}"
echo -e "${BLUE}║                                                                ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""

# Function to check if command exists
command_exists() {
    command -v "$1" &> /dev/null
}

# Step 1: Check prerequisites
echo -e "${YELLOW}[1/7] Checking prerequisites...${NC}"

if ! command_exists minikube; then
    echo -e "${RED}Error: minikube not found. Please install it first.${NC}"
    echo "Run: ./scripts/setup-minikube.sh"
    exit 1
fi

if ! command_exists kubectl; then
    echo -e "${RED}Error: kubectl not found. Please install it first.${NC}"
    exit 1
fi

if ! command_exists docker; then
    echo -e "${RED}Error: docker not found. Please install it first.${NC}"
    exit 1
fi

echo -e "${GREEN}✓ All prerequisites found${NC}"
echo ""

# Step 2: Start/Restart Minikube
echo -e "${YELLOW}[2/7] Starting Minikube...${NC}"

# Check if Minikube is already running
if minikube status 2>/dev/null | grep -q "Running"; then
    echo -e "${CYAN}Minikube is already running. Restarting...${NC}"
    minikube stop
    sleep 3
fi

# Start Minikube
START_CMD="minikube start --driver=docker"
[ -n "$MEMORY" ] && START_CMD="$START_CMD --memory=$MEMORY"
[ -n "$CPUS" ] && START_CMD="$START_CMD --cpus=$CPUS"

if [ -n "$MEMORY" ] || [ -n "$CPUS" ]; then
    echo -e "${CYAN}Starting Minikube with custom resources...${NC}"
    [ -n "$MEMORY" ] && echo -e "${CYAN}  Memory: ${MEMORY}MB${NC}"
    [ -n "$CPUS" ] && echo -e "${CYAN}  CPUs: ${CPUS}${NC}"
else
    echo -e "${CYAN}Starting Minikube with no resource limits (using all available)...${NC}"
fi

newgrp docker << EONG
$START_CMD
EONG

if [ $? -ne 0 ]; then
    echo -e "${RED}Failed to start Minikube${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Minikube started successfully${NC}"
echo ""

# Step 3: Wait for Kubernetes to be ready
echo -e "${YELLOW}[3/7] Waiting for Kubernetes to be ready...${NC}"
kubectl wait --for=condition=Ready node/minikube --timeout=120s
echo -e "${GREEN}✓ Kubernetes is ready${NC}"
echo ""

# Step 4: Restart all pods if they exist
echo -e "${YELLOW}[4/7] Checking and restarting pods...${NC}"

# Check if sock-shop namespace exists
if kubectl get namespace sock-shop &> /dev/null; then
    echo -e "${CYAN}Restarting Sock Shop deployments...${NC}"
    kubectl rollout restart deployment -n sock-shop 2>/dev/null || true
    
    echo -e "${CYAN}Waiting for Sock Shop pods to be ready...${NC}"
    sleep 10
    kubectl wait --for=condition=Ready pods --all -n sock-shop --timeout=180s 2>/dev/null || {
        echo -e "${YELLOW}Some pods may still be starting...${NC}"
    }
else
    echo -e "${YELLOW}Sock Shop not deployed yet. Run: ./scripts/deploy-sock-shop.sh${NC}"
fi

# Check if istio-system namespace exists
if kubectl get namespace istio-system &> /dev/null; then
    echo -e "${CYAN}Restarting Istio system deployments...${NC}"
    kubectl rollout restart deployment -n istio-system 2>/dev/null || true
    
    echo -e "${CYAN}Waiting for Istio pods to be ready...${NC}"
    sleep 10
    kubectl wait --for=condition=Ready pods --all -n istio-system --timeout=180s 2>/dev/null || {
        echo -e "${YELLOW}Some Istio pods may still be starting...${NC}"
    }
else
    echo -e "${YELLOW}Istio not deployed yet. Run: ./scripts/setup-istio.sh${NC}"
fi

echo -e "${GREEN}✓ Pods restarted${NC}"
echo ""

# Step 5: Get connection info
echo -e "${YELLOW}[5/7] Getting connection information...${NC}"

MINIKUBE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null)
FRONTEND_NODEPORT=$(kubectl get svc front-end -n sock-shop -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || echo "")

if [ -n "$MINIKUBE_IP" ] && [ -n "$FRONTEND_NODEPORT" ]; then
    echo -e "${GREEN}✓ Frontend URL: http://${MINIKUBE_IP}:${FRONTEND_NODEPORT}${NC}"
else
    echo -e "${YELLOW}⚠ Frontend service not accessible yet${NC}"
fi
echo ""

# Step 6: Setup port forwarding
echo -e "${YELLOW}[6/7] Setting up port forwarding...${NC}"

# Kill any existing port forwards
echo -e "${CYAN}Cleaning up existing port forwards...${NC}"
pkill -f "kubectl port-forward" 2>/dev/null || true
sleep 2

# Track PIDs for cleanup
PORT_FORWARD_PIDS=()

# Frontend port forward (optional - NodePort already works)
if kubectl get svc front-end -n sock-shop &> /dev/null; then
    echo -e "${CYAN}Setting up Frontend port forward (8080 → 80)...${NC}"
    kubectl port-forward -n sock-shop svc/front-end 8080:80 &> /tmp/pf-frontend.log &
    PORT_FORWARD_PIDS+=($!)
    sleep 1
fi

# Grafana
if kubectl get svc grafana -n istio-system &> /dev/null; then
    echo -e "${CYAN}Setting up Grafana port forward (3000 → 3000)...${NC}"
    kubectl port-forward -n istio-system svc/grafana 3000:3000 &> /tmp/pf-grafana.log &
    PORT_FORWARD_PIDS+=($!)
    sleep 1
else
    echo -e "${YELLOW}⚠ Grafana service not found${NC}"
fi

# Prometheus
if kubectl get svc prometheus -n istio-system &> /dev/null; then
    echo -e "${CYAN}Setting up Prometheus port forward (9090 → 9090)...${NC}"
    kubectl port-forward -n istio-system svc/prometheus 9090:9090 &> /tmp/pf-prometheus.log &
    PORT_FORWARD_PIDS+=($!)
    sleep 1
else
    echo -e "${YELLOW}⚠ Prometheus service not found${NC}"
fi

# Kiali
if kubectl get svc kiali -n istio-system &> /dev/null; then
    echo -e "${CYAN}Setting up Kiali port forward (20001 → 20001)...${NC}"
    kubectl port-forward -n istio-system svc/kiali 20001:20001 &> /tmp/pf-kiali.log &
    PORT_FORWARD_PIDS+=($!)
    sleep 1
else
    echo -e "${YELLOW}⚠ Kiali service not found${NC}"
fi

# Jaeger (if exists)
if kubectl get svc tracing -n istio-system &> /dev/null; then
    echo -e "${CYAN}Setting up Jaeger port forward (16686 → 80)...${NC}"
    kubectl port-forward -n istio-system svc/tracing 16686:80 &> /tmp/pf-jaeger.log &
    PORT_FORWARD_PIDS+=($!)
    sleep 1
else
    echo -e "${YELLOW}⚠ Jaeger service not found${NC}"
fi

echo -e "${GREEN}✓ Port forwarding setup complete${NC}"
echo ""

# Step 7: Display summary
echo -e "${YELLOW}[7/7] Summary${NC}"
echo ""
echo -e "${BLUE}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║                  ${GREEN}Access Information${BLUE}                          ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""

if [ -n "$MINIKUBE_IP" ] && [ -n "$FRONTEND_NODEPORT" ]; then
    echo -e "${CYAN}Frontend (NodePort):${NC}"
    echo -e "  ${GREEN}http://${MINIKUBE_IP}:${FRONTEND_NODEPORT}${NC}"
    echo ""
fi

echo -e "${CYAN}Frontend (Port Forward):${NC}"
echo -e "  ${GREEN}http://localhost:8080${NC}"
echo ""

echo -e "${CYAN}Monitoring Dashboards:${NC}"
echo -e "  Grafana:    ${GREEN}http://localhost:3000${NC} (admin/admin)"
echo -e "  Prometheus: ${GREEN}http://localhost:9090${NC}"
echo -e "  Kiali:      ${GREEN}http://localhost:20001${NC} (admin/admin)"
echo -e "  Jaeger:     ${GREEN}http://localhost:16686${NC}"
echo ""

echo -e "${CYAN}Port Forward Processes:${NC}"
ps aux | grep "[k]ubectl port-forward" | awk '{print "  PID " $2 ": " $13 " " $14 " " $15 " " $16}'
echo ""

echo -e "${CYAN}Current Status:${NC}"
echo ""
echo -e "${YELLOW}Sock Shop Pods:${NC}"
kubectl get pods -n sock-shop 2>/dev/null | head -10 || echo "  Not deployed"
echo ""
echo -e "${YELLOW}Istio Pods:${NC}"
kubectl get pods -n istio-system 2>/dev/null | grep -E "NAME|grafana|prometheus|kiali|jaeger" || echo "  Not deployed"
echo ""

echo -e "${BLUE}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║                    ${GREEN}Important Notes${BLUE}                            ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${YELLOW}•${NC} Port forwards are running in the background"
echo -e "${YELLOW}•${NC} To stop all port forwards: ${GREEN}pkill -f 'kubectl port-forward'${NC}"
echo -e "${YELLOW}•${NC} To view port forward logs: ${GREEN}tail -f /tmp/pf-*.log${NC}"
echo -e "${YELLOW}•${NC} Port forward PIDs: ${GREEN}${PORT_FORWARD_PIDS[*]}${NC}"
echo ""

# Save PID file for easy cleanup
echo "${PORT_FORWARD_PIDS[*]}" > /tmp/port-forward-pids.txt

echo -e "${GREEN}✅ Setup complete!${NC}"
echo ""
echo -e "To stop port forwards later, run:"
echo -e "  ${GREEN}kill \$(cat /tmp/port-forward-pids.txt)${NC}"
echo -e "  or"
echo -e "  ${GREEN}pkill -f 'kubectl port-forward'${NC}"
echo ""
