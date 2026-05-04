#!/bin/bash
"""Unified Test Script for DDoS Detection"""

set -e

DETECTOR_SCRIPT="${1:-detector.py}"
ATTACK_SCRIPT="${2:-attack-simulations/attack.py}"
SOCK_SHOP_URL="http://$(minikube ip):30001"
PROMETHEUS_URL="http://localhost:9090"
LOG_FILE="detector.log"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

cleanup() {
    echo -e "\n${YELLOW}Cleaning up...${NC}"
    pkill -f "${DETECTOR_SCRIPT}" 2>/dev/null || true
    pkill -f "${ATTACK_SCRIPT}" 2>/dev/null || true
    pkill -f "port-forward.*prometheus" 2>/dev/null || true
    sleep 2
}
trap cleanup EXIT

echo -e "${BLUE}=== DDoS Detection Test ===${NC}\n"

if ! minikube status &> /dev/null; then
    echo -e "${RED}Error: Minikube not running${NC}"
    exit 1
fi

echo "Starting Prometheus port-forward..."
kubectl port-forward -n istio-system svc/prometheus 9090:9090 > /dev/null 2>&1 &
sleep 3

if ! curl -s "${PROMETHEUS_URL}/-/ready" &> /dev/null; then
    echo -e "${RED}Error: Prometheus not accessible${NC}"
    exit 1
fi

echo "Starting detector..."
> "${LOG_FILE}"
cd /home/spuggle/dev/ddos/ml-detection
python3 "${DETECTOR_SCRIPT}" --prometheus-url "${PROMETHEUS_URL}" --interval 30 > /dev/null 2>&1 &
DETECTOR_PID=$!
sleep 5

echo -e "${GREEN}✓ Detector running (PID: ${DETECTOR_PID})${NC}"
echo "Collecting baseline (90s)..."
sleep 90

echo -e "\n${BLUE}Launching attack...${NC}"
ATTACK_TYPE="${3:-http-flood}"
python3 "${ATTACK_SCRIPT}" \
    --target-url "${SOCK_SHOP_URL}" \
    --attack-type "${ATTACK_TYPE}" \
    --duration 30 \
    --workers 20 \
    --rate 10 > /dev/null 2>&1 &

echo "Monitoring for detection (60s)..."
BEFORE=$(grep -c "DDOS ATTACK DETECTED" "${LOG_FILE}" 2>/dev/null || echo "0")
sleep 60
AFTER=$(grep -c "DDOS ATTACK DETECTED" "${LOG_FILE}" 2>/dev/null || echo "0")
DETECTED=$((AFTER - BEFORE))

if [ "${DETECTED}" -gt 0 ]; then
    echo -e "\n${GREEN}✓ Attack Detected!${NC}\n"
    grep -A 15 "DDOS ATTACK DETECTED" "${LOG_FILE}" | tail -20
else
    echo -e "\n${RED}✗ Attack Not Detected${NC}\n"
    tail -20 "${LOG_FILE}"
fi

echo -e "\n${BLUE}Test Complete${NC}"
echo "Full log: ${LOG_FILE}"
