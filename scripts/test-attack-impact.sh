#!/usr/bin/env bash
#
# Quick Attack Impact Test
# =========================
# Test attack parameters to find the right intensity to cause failures
#

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Configuration - Override with environment variables
NAMESPACE="${NAMESPACE:-sock-shop}"
DURATION="${DURATION:-60}"  # Short test - 60 seconds
WORKERS="${WORKERS:-50}"
RATE="${RATE:-50}"

TOTAL_RATE=$((WORKERS * RATE))

echo -e "${CYAN}╔═══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║          QUICK ATTACK IMPACT TEST                             ║${NC}"
echo -e "${CYAN}╚═══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo "Configuration:"
echo "  Duration: ${DURATION}s"
echo "  Workers: ${WORKERS}"
echo "  Rate: ${RATE} req/s/worker"
echo "  Total Target Rate: ${TOTAL_RATE} req/s"
echo ""

# Check prerequisites
if ! command -v kubectl &> /dev/null; then
    echo -e "${RED}✗ kubectl not found${NC}"
    exit 1
fi

if ! kubectl cluster-info &> /dev/null; then
    echo -e "${RED}✗ Kubernetes cluster not accessible${NC}"
    exit 1
fi

# Get NodePort URL for external access (realistic attack)
echo -e "${YELLOW}Getting external NodePort URL...${NC}"
MINIKUBE_IP=$(minikube ip)
NODE_PORT=$(kubectl get svc -n "$NAMESPACE" front-end -o jsonpath='{.spec.ports[0].nodePort}')
TARGET_URL="http://${MINIKUBE_IP}:${NODE_PORT}"

echo -e "${GREEN}✓ Using NodePort for external access: $TARGET_URL${NC}"
echo -e "  ${CYAN}(Simulating external DDoS attack)${NC}"
echo ""

# Cleanup function
cleanup() {
    echo ""
    echo -e "${YELLOW}Cleanup complete${NC}"
}
trap cleanup EXIT

# Show system state before attack
echo -e "${CYAN}System State (Before Attack):${NC}"
echo "Pod count: $(kubectl get pods -n $NAMESPACE --no-headers 2>/dev/null | wc -l)"
CPU_USAGE=$(kubectl top pods -n $NAMESPACE --no-headers 2>/dev/null | awk '{sum+=$2} END {print sum}' | sed 's/m//')
echo "Total CPU: ${CPU_USAGE}m"
echo ""

# Run attack
echo -e "${GREEN}▶ Launching Attack${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

ATTACK_LOG="/tmp/attack-test-$(date +%s).log"

cd "$PROJECT_ROOT"
python3 attacks/crossfire_enhanced.py \
    --url "$TARGET_URL" \
    --discovery-file attacks/discovered-endpoints.json \
    --duration "$DURATION" \
    --workers "$WORKERS" \
    --rate "$RATE" \
    --mode moderate \
    2>&1 | tee "$ATTACK_LOG"

echo ""
echo -e "${CYAN}System State (After Attack):${NC}"
echo "Pod count: $(kubectl get pods -n $NAMESPACE --no-headers 2>/dev/null | wc -l)"
CPU_USAGE_AFTER=$(kubectl top pods -n $NAMESPACE --no-headers 2>/dev/null | awk '{sum+=$2} END {print sum}' | sed 's/m//')
echo "Total CPU: ${CPU_USAGE_AFTER}m"
echo ""

# Parse results
echo -e "${GREEN}╔═══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                    ATTACK RESULTS                             ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════════════════════════╝${NC}"

TOTAL_REQUESTS=$(grep "Total Requests:" "$ATTACK_LOG" | awk '{print $3}' | tr -d ',')
SUCCESSFUL=$(grep "Successful:" "$ATTACK_LOG" | awk '{print $2}' | tr -d ',')
FAILED=$(grep "Failed:" "$ATTACK_LOG" | awk '{print $2}' | tr -d ',')
SUCCESS_RATE=$(grep "Successful:" "$ATTACK_LOG" | grep -oP '\(\K[0-9.]+' || echo "0")
FAILURE_RATE=$(grep "Failed:" "$ATTACK_LOG" | grep -oP '\(\K[0-9.]+' || echo "0")
ACTUAL_RATE=$(grep "Actual Rate:" "$ATTACK_LOG" | awk '{print $3}')
AVG_LATENCY=$(grep "Avg Latency:" "$ATTACK_LOG" | awk '{print $3}')

echo "Total Requests: $TOTAL_REQUESTS"
echo "Successful: $SUCCESSFUL (${SUCCESS_RATE}%)"
echo "Failed: $FAILED (${FAILURE_RATE}%)"
echo "Actual Rate: $ACTUAL_RATE req/s (target: $TOTAL_RATE req/s)"
echo "Avg Latency: $AVG_LATENCY"
echo ""

# Evaluate impact
if (( $(echo "$FAILURE_RATE > 30" | bc -l) )); then
    echo -e "${RED}🔥 HIGH IMPACT: ${FAILURE_RATE}% failure rate - System overwhelmed!${NC}"
    echo "   This attack intensity is good for testing mitigations."
elif (( $(echo "$FAILURE_RATE > 10" | bc -l) )); then
    echo -e "${YELLOW}⚠️  MODERATE IMPACT: ${FAILURE_RATE}% failure rate${NC}"
    echo "   Consider increasing attack rate to show clearer differences."
elif (( $(echo "$FAILURE_RATE > 0" | bc -l) )); then
    echo -e "${YELLOW}✓ LOW IMPACT: ${FAILURE_RATE}% failure rate${NC}"
    echo "   Increase workers or rate to stress the system more."
else
    echo -e "${GREEN}✓ NO IMPACT: 0% failure rate - System handling load easily${NC}"
    echo "   Significantly increase attack parameters:"
    echo "   - Try doubling workers: WORKERS=$((WORKERS * 2))"
    echo "   - Try doubling rate: RATE=$((RATE * 2))"
    echo "   - Or both: WORKERS=$((WORKERS * 2)) RATE=$((RATE * 2))"
fi

echo ""
echo -e "${CYAN}To rerun with different parameters:${NC}"
echo "  WORKERS=100 RATE=100 $0"
echo "  WORKERS=200 RATE=100 $0"
echo "  WORKERS=500 RATE=50 $0"
echo ""
echo "Attack log saved to: $ATTACK_LOG"
