#!/bin/bash

# Run endpoint discovery to map the target application
# This should be run first before launching attacks

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default values
TARGET="${1:-}"
MAX_DEPTH="${2:-2}"
OUTPUT="${3:-discovered-endpoints.json}"

echo -e "${BLUE}=== Endpoint Discovery Scenario ===${NC}"
echo ""

if [ -z "$TARGET" ]; then
    # Try to get Minikube IP and NodePort
    MINIKUBE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null)
    NODEPORT=$(kubectl get svc front-end -n sock-shop -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null)
    
    if [ -n "$MINIKUBE_IP" ] && [ -n "$NODEPORT" ]; then
        TARGET="http://${MINIKUBE_IP}:${NODEPORT}"
        echo -e "${GREEN}Auto-detected target: ${TARGET}${NC}"
    else
        echo -e "${RED}Error: Cannot auto-detect target. Please provide target URL.${NC}"
        echo "Usage: $0 <target-url> [max-depth] [output-file]"
        echo "Example: $0 http://192.168.49.2:30001 2 discovered-endpoints.json"
        exit 1
    fi
fi

echo -e "${GREEN}Target: ${TARGET}${NC}"
echo -e "${GREEN}Max Depth: ${MAX_DEPTH}${NC}"
echo -e "${GREEN}Output File: ${OUTPUT}${NC}"
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

echo -e "${YELLOW}Starting endpoint discovery...${NC}"
echo ""

python3 attack-simulations/endpoint-discovery.py \
    --target "$TARGET" \
    --max-depth "$MAX_DEPTH" \
    --output "attack-simulations/$OUTPUT"

DISCOVERY_STATUS=$?

deactivate

if [ $DISCOVERY_STATUS -eq 0 ]; then
    echo ""
    echo -e "${GREEN}=== Discovery Complete ===${NC}"
    echo -e "Results saved to: ${GREEN}attack-simulations/$OUTPUT${NC}"
    echo ""
    echo -e "${BLUE}Next steps:${NC}"
    echo -e "  1. Review discovered endpoints:"
    echo -e "     ${GREEN}cat attack-simulations/$OUTPUT | jq '.recommended_targets'${NC}"
    echo -e "  2. Run application-level attack:"
    echo -e "     ${GREEN}./scripts/run-scenario-app-attack.sh${NC}"
    echo -e "  3. Run network-level attack:"
    echo -e "     ${GREEN}sudo ./scripts/run-scenario-network-attack.sh${NC}"
    echo ""
else
    echo ""
    echo -e "${RED}=== Discovery Failed ===${NC}"
    exit 1
fi
