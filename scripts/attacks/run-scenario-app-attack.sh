#!/bin/bash

# Run application-level crossfire DDoS attack
# Uses discovered endpoints if available, otherwise uses target URL directly

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default values
DURATION="${1:-300}"
DECOY_COUNT="${2:-100}"
BOT_THREADS="${3:-50}"
FLOOD_RATE="${4:-100}"
TARGETS_FILE="${5:-attack-simulations/discovered-endpoints.json}"

echo -e "${BLUE}=== Application-Level Crossfire Attack Scenario ===${NC}"
echo ""

# Check if Python3 is installed
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Error: Python3 is not installed${NC}"
    exit 1
fi

# Check if virtual environment exists
if [ ! -d "attack-simulations/venv" ]; then
    echo -e "${YELLOW}Creating Python virtual environment...${NC}"
    python3 -m venv attack-simulations/venv
    source attack-simulations/venv/bin/activate
    pip install -q -r attack-simulations/requirements.txt
else
    source attack-simulations/venv/bin/activate
fi

# Get target URL
if [ -f "$TARGETS_FILE" ]; then
    echo -e "${GREEN}Using discovered endpoints from: ${TARGETS_FILE}${NC}"
    TARGET_URL=$(python3 -c "import json; print(json.load(open('$TARGETS_FILE'))['base_url'])" 2>/dev/null || echo "")
    USE_TARGETS_FILE="yes"
else
    echo -e "${YELLOW}No discovered endpoints found. Run discovery first:${NC}"
    echo -e "  ${GREEN}./scripts/run-scenario-discovery.sh${NC}"
    echo ""
    
    # Try to get Minikube IP and NodePort
    MINIKUBE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null)
    NODEPORT=$(kubectl get svc front-end -n sock-shop -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null)
    
    if [ -n "$MINIKUBE_IP" ] && [ -n "$NODEPORT" ]; then
        TARGET_URL="http://${MINIKUBE_IP}:${NODEPORT}"
        echo -e "${YELLOW}Falling back to auto-detected target: ${TARGET_URL}${NC}"
        USE_TARGETS_FILE="no"
    else
        echo -e "${RED}Error: Cannot determine target URL${NC}"
        exit 1
    fi
fi

if [ -z "$TARGET_URL" ]; then
    echo -e "${RED}Error: Cannot determine target URL${NC}"
    exit 1
fi

echo -e "${GREEN}Target URL: ${TARGET_URL}${NC}"
echo -e "${GREEN}Duration: ${DURATION} seconds${NC}"
echo -e "${GREEN}Decoy Links: ${DECOY_COUNT}${NC}"
echo -e "${GREEN}Bot Threads: ${BOT_THREADS}${NC}"
echo -e "${GREEN}Flood Rate: ${FLOOD_RATE} req/s${NC}"
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

read -p "Press Enter to start the attack (Ctrl+C to cancel)..."
echo ""

echo -e "${YELLOW}Starting Application-Level Crossfire Attack...${NC}"
echo ""

# Build command
if [ "$USE_TARGETS_FILE" = "yes" ]; then
    python3 attack-simulations/crossfire-app-level.py \
        --target "$TARGET_URL" \
        --targets-file "$TARGETS_FILE" \
        --duration "$DURATION" \
        --decoys "$DECOY_COUNT" \
        --bot-threads "$BOT_THREADS" \
        --flood-rate "$FLOOD_RATE"
else
    python3 attack-simulations/crossfire-app-level.py \
        --target "$TARGET_URL" \
        --duration "$DURATION" \
        --decoys "$DECOY_COUNT" \
        --bot-threads "$BOT_THREADS" \
        --flood-rate "$FLOOD_RATE"
fi

ATTACK_STATUS=$?

deactivate

echo ""
if [ $ATTACK_STATUS -eq 0 ]; then
    echo -e "${GREEN}=== Attack Complete ===${NC}"
    echo -e "Check monitoring dashboards to analyze the impact."
else
    echo -e "${RED}=== Attack Failed ===${NC}"
    exit 1
fi
echo ""
