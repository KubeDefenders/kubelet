#!/bin/bash
"""Start Locust Load Testing for Sock Shop"""

VENV_DIR="/home/spuggle/dev/ddos/venv"
LOCUSTFILE="locustfile.py"
ISTIO_PORT=31987

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${BLUE}╔══════════════════════════════════════╗${NC}"
echo -e "${BLUE}║   Locust Traffic Generator          ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════╝${NC}"
echo ""

# Get Minikube IP
MINIKUBE_IP=$(minikube ip 2>/dev/null)
if [ -z "$MINIKUBE_IP" ]; then
    echo -e "${RED}✗ Minikube not running${NC}"
    echo -e "${YELLOW}Start with: minikube start${NC}"
    exit 1
fi

TARGET_URL="http://${MINIKUBE_IP}:${ISTIO_PORT}"

echo -e "${GREEN}✓ Target: ${TARGET_URL}${NC}"
echo ""

# Parse arguments or use defaults
USERS=${1:-10}
SPAWN_RATE=${2:-2}
RUN_TIME=${3:-10m}
MODE=${4:-headless}

echo -e "${CYAN}Configuration:${NC}"
echo -e "  Users: ${USERS}"
echo -e "  Spawn Rate: ${SPAWN_RATE} users/sec"
echo -e "  Run Time: ${RUN_TIME}"
echo -e "  Mode: ${MODE}"
echo ""

if [ "$MODE" = "web" ]; then
    echo -e "${YELLOW}Starting Locust with Web UI...${NC}"
    echo -e "${GREEN}Access dashboard at: ${NC}${CYAN}http://localhost:8089${NC}"
    echo ""
    "${VENV_DIR}/bin/locust" \
        -f "${LOCUSTFILE}" \
        --host "${TARGET_URL}" \
        --web-host 0.0.0.0 \
        --web-port 8089
else
    echo -e "${YELLOW}Starting Locust (headless mode)...${NC}"
    echo ""
    "${VENV_DIR}/bin/locust" \
        -f "${LOCUSTFILE}" \
        --host "${TARGET_URL}" \
        --users "${USERS}" \
        --spawn-rate "${SPAWN_RATE}" \
        --run-time "${RUN_TIME}" \
        --headless \
        --html locust-report.html
    
    echo ""
    echo -e "${GREEN}✓ Load test complete!${NC}"
    echo -e "${CYAN}Report saved to: locust-report.html${NC}"
fi
