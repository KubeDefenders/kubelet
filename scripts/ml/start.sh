#!/bin/bash
"""Start Detection System"""

set -e

VENV_DIR="/home/spuggle/dev/ddos/venv"
PROMETHEUS_URL="http://localhost:9090"
LOG_FILE="ml-detection/detector.log"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

cleanup() {
    echo -e "\n${YELLOW}Stopping services...${NC}"
    pkill -f "detector.py" 2>/dev/null || true
    pkill -f "port-forward.*prometheus" 2>/dev/null || true
    sleep 2
    echo -e "${GREEN}Stopped${NC}"
}
trap cleanup EXIT

echo -e "${BLUE}╔══════════════════════════════════════╗${NC}"
echo -e "${BLUE}║   DDoS Detection System Startup      ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════╝${NC}"
echo ""

if ! minikube status &> /dev/null; then
    echo -e "${RED}✗ Minikube not running${NC}"
    echo -e "${YELLOW}Start with: minikube start${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Minikube running${NC}"

echo ""
echo -e "${YELLOW}Starting Prometheus port-forward...${NC}"
kubectl port-forward -n istio-system svc/prometheus 9090:9090 > /dev/null 2>&1 &
sleep 3

if ! curl -s "${PROMETHEUS_URL}/-/ready" &> /dev/null; then
    echo -e "${RED}✗ Prometheus not accessible${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Prometheus accessible at ${PROMETHEUS_URL}${NC}"

echo ""
echo -e "${YELLOW}Starting detector...${NC}"
> "${LOG_FILE}"
cd ml-detection
"${VENV_DIR}/bin/python3" detector.py --prometheus-url "${PROMETHEUS_URL}" --interval 30 >> "../${LOG_FILE}" 2>&1 &
DETECTOR_PID=$!
cd ..
sleep 3

if ps -p ${DETECTOR_PID} > /dev/null; then
    echo -e "${GREEN}✓ Detector running (PID: ${DETECTOR_PID})${NC}"
else
    echo -e "${RED}✗ Detector failed to start${NC}"
    tail -20 "${LOG_FILE}"
    exit 1
fi

echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}✓ Detection system started successfully${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "${YELLOW}Collecting baseline (90 seconds)...${NC}"
echo -e "  The detector learns normal traffic patterns before alerting."
echo ""

for i in {90..1}; do
    echo -ne "\r  Baseline collection: ${i}s remaining...  "
    sleep 1
done
echo -e "\r${GREEN}✓ Baseline collection complete${NC}                    "

echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}System Ready!${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo ""
echo -e "1. Generate normal traffic (optional):"
echo -e "   ${CYAN}python3 traffic-generator.py --target-url http://\$(minikube ip):30001${NC}"
echo ""
echo -e "2. Monitor detection in another terminal:"
echo -e "   ${CYAN}./monitor.sh${NC}"
echo ""
echo -e "3. Launch an attack in another terminal:"
echo -e "   ${CYAN}python3 attack-simulations/attack.py --target-url http://\$(minikube ip):30001 --attack-type http-flood${NC}"
echo ""
echo -e "4. Check detection log:"
echo -e "   ${CYAN}tail -f ${LOG_FILE}${NC}"
echo ""
echo -e "${YELLOW}Press Ctrl+C to stop the detector${NC}"
echo ""

wait ${DETECTOR_PID}
