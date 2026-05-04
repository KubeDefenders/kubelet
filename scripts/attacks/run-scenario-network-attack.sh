#!/bin/bash

# Run network-level crossfire DDoS attack
# Requires root privileges for raw socket access

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo -e "${RED}Error: Network-level attack requires root privileges${NC}"
    echo -e "Please run: ${GREEN}sudo $0 $@${NC}"
    exit 1
fi

# Default values
DURATION="${1:-300}"
DECOY_COUNT="${2:-100}"
PACKET_RATE="${3:-1000}"
TARGETS_FILE="${4:-attack-simulations/discovered-endpoints.json}"

echo -e "${BLUE}=== Network-Level Crossfire Attack Scenario ===${NC}"
echo ""

# Check if Python3 is installed
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Error: Python3 is not installed${NC}"
    exit 1
fi

# Get target IP and port
if [ -f "$TARGETS_FILE" ]; then
    echo -e "${GREEN}Using discovered endpoints from: ${TARGETS_FILE}${NC}"
    TARGET_URL=$(python3 -c "import json; print(json.load(open('$TARGETS_FILE'))['base_url'])" 2>/dev/null || echo "")
else
    echo -e "${YELLOW}No discovered endpoints found.${NC}"
    
    # Try to get Minikube IP and NodePort
    MINIKUBE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null)
    NODEPORT=$(kubectl get svc front-end -n sock-shop -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null)
    
    if [ -n "$MINIKUBE_IP" ] && [ -n "$NODEPORT" ]; then
        TARGET_URL="http://${MINIKUBE_IP}:${NODEPORT}"
        echo -e "${YELLOW}Using auto-detected target: ${TARGET_URL}${NC}"
    else
        echo -e "${RED}Error: Cannot determine target${NC}"
        exit 1
    fi
fi

# Parse IP and port from URL
TARGET_IP=$(echo "$TARGET_URL" | sed -E 's|^https?://([^:/]+).*|\1|')
TARGET_PORT=$(echo "$TARGET_URL" | sed -E 's|^https?://[^:]+:([0-9]+).*|\1|')

# Validate we got both
if [ -z "$TARGET_IP" ] || [ -z "$TARGET_PORT" ]; then
    echo -e "${RED}Error: Cannot parse target IP and port from: ${TARGET_URL}${NC}"
    exit 1
fi

echo -e "${GREEN}Target IP: ${TARGET_IP}${NC}"
echo -e "${GREEN}Target Port: ${TARGET_PORT}${NC}"
echo -e "${GREEN}Duration: ${DURATION} seconds${NC}"
echo -e "${GREEN}Decoy Links: ${DECOY_COUNT}${NC}"
echo -e "${GREEN}Packet Rate: ${PACKET_RATE} pkt/s${NC}"
echo ""

# Show monitoring info
echo -e "${BLUE}=== Monitoring Information ===${NC}"
echo -e "Monitor the attack effects using:"
echo -e "  ${GREEN}Grafana:${NC}    kubectl port-forward -n istio-system svc/grafana 3000:3000"
echo -e "  ${GREEN}Kiali:${NC}      kubectl port-forward -n istio-system svc/kiali 20001:20001"
echo -e "  ${GREEN}Prometheus:${NC} kubectl port-forward -n istio-system svc/prometheus-k8s 9090:9090"
echo ""
echo -e "Or run: ${GREEN}./scripts/access-monitoring.sh${NC}"
echo ""

echo -e "${YELLOW}⚠️  WARNING: This will send raw network packets${NC}"
read -p "Press Enter to start the attack (Ctrl+C to cancel)..."
echo ""

echo -e "${YELLOW}Starting Network-Level Crossfire Attack...${NC}"
echo ""

# Check if virtual environment exists
if [ ! -d "attack-simulations/venv" ]; then
    echo -e "${YELLOW}Creating Python virtual environment...${NC}"
    python3 -m venv attack-simulations/venv
    # Note: running as root, so venv ownership might be root
    attack-simulations/venv/bin/pip install -q -r attack-simulations/requirements.txt
fi

# Run with targets file if available
if [ -f "$TARGETS_FILE" ]; then
    attack-simulations/venv/bin/python3 attack-simulations/crossfire-network-level.py \
        --target "$TARGET_IP" \
        --target-port "$TARGET_PORT" \
        --targets-file "$TARGETS_FILE" \
        --duration "$DURATION" \
        --decoys "$DECOY_COUNT" \
        --packet-rate "$PACKET_RATE"
else
    attack-simulations/venv/bin/python3 attack-simulations/crossfire-network-level.py \
        --target "$TARGET_IP" \
        --target-port "$TARGET_PORT" \
        --duration "$DURATION" \
        --decoys "$DECOY_COUNT" \
        --packet-rate "$PACKET_RATE"
fi

ATTACK_STATUS=$?

echo ""
if [ $ATTACK_STATUS -eq 0 ]; then
    echo -e "${GREEN}=== Attack Complete ===${NC}"
    echo -e "Check monitoring dashboards to analyze the impact."
else
    echo -e "${RED}=== Attack Failed ===${NC}"
    exit 1
fi
echo ""
