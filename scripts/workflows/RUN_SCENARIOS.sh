#!/bin/bash
# DDoS Attack Testing - All Scenarios
# This script runs all three test scenarios sequentially

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
NC='\033[0m'

# Configuration
DURATION=30
RATE=50
WORKERS=100
NAMESPACE="sock-shop"

echo -e "${MAGENTA}╔══════════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${MAGENTA}║          DDoS Attack Testing - All Scenarios                         ║${NC}"
echo -e "${MAGENTA}╚══════════════════════════════════════════════════════════════════════╝${NC}"
echo ""

# Prerequisites check
echo -e "${CYAN}Checking prerequisites...${NC}"

if ! minikube status &> /dev/null; then
    echo -e "${RED}✗ Minikube is not running${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Minikube is running${NC}"

if ! kubectl get namespace $NAMESPACE &> /dev/null; then
    echo -e "${RED}✗ Namespace $NAMESPACE not found${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Namespace $NAMESPACE exists${NC}"

# Get target URL
if [ -z "$TARGET_URL" ]; then
    # Try to get NodePort directly (works with Docker driver)
    NODEPORT=$(kubectl get svc front-end -n $NAMESPACE -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null)
    if [ -n "$NODEPORT" ]; then
        # Set up port forwarding for Docker driver
        echo -e "${YELLOW}Setting up port forwarding (Docker driver detected)...${NC}"
        kubectl port-forward -n $NAMESPACE svc/front-end 8080:80 > /dev/null 2>&1 &
        PORT_FORWARD_PID=$!
        sleep 5
        TARGET_URL="http://localhost:8080"
        echo -e "${GREEN}✓ Port forwarding started (PID: $PORT_FORWARD_PID)${NC}"
    else
        echo -e "${RED}✗ Cannot get front-end service${NC}"
        exit 1
    fi
fi

# Verify service is accessible
echo -e "${YELLOW}Verifying service is accessible...${NC}"
for i in {1..5}; do
    if curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$TARGET_URL" | grep -q "200\|302"; then
        echo -e "${GREEN}✓ Target URL: $TARGET_URL${NC}"
        break
    else
        if [ $i -eq 5 ]; then
            echo -e "${RED}✗ Service not accessible at $TARGET_URL${NC}"
            echo -e "${RED}✗ Please check if pods are running: kubectl get pods -n $NAMESPACE${NC}"
            [ -n "$PORT_FORWARD_PID" ] && kill $PORT_FORWARD_PID 2>/dev/null
            exit 1
        fi
        echo -e "${YELLOW}Retry $i/5...${NC}"
        sleep 2
    fi
done

# Check Python environment
if [ ! -d ".venv" ]; then
    echo -e "${RED}✗ Python virtual environment not found${NC}"
    exit 1
fi
source .venv/bin/activate
echo -e "${GREEN}✓ Python environment activated${NC}"
echo ""

# Function to wait for user
wait_for_user() {
    echo ""
    echo -e "${YELLOW}Press Enter to continue to next scenario...${NC}"
    read
}

# Function to clean all mitigations
clean_all() {
    echo -e "${YELLOW}Cleaning all mitigations...${NC}"
    kubectl delete hpa --all -n $NAMESPACE &> /dev/null || true
    kubectl delete networkpolicies --all -n $NAMESPACE &> /dev/null || true
    kubectl delete resourcequotas --all -n $NAMESPACE &> /dev/null || true
    kubectl delete priorityclasses -l nephio.org/managed=true &> /dev/null || true
    sleep 10
    echo -e "${GREEN}✓ Cleanup complete${NC}"
}

# Function to check pod status
check_pods() {
    echo -e "${CYAN}Current pod status:${NC}"
    kubectl get pods -n $NAMESPACE | grep -E "front-end|catalogue|carts"
}

#==============================================================================
# SCENARIO 1: BASELINE - NO MITIGATION
#==============================================================================

echo -e "${MAGENTA}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${MAGENTA}   SCENARIO 1: BASELINE (No Mitigation)${NC}"
echo -e "${MAGENTA}═══════════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "${CYAN}Purpose:${NC} Establish baseline performance under crossfire attack"
echo -e "${CYAN}Expected:${NC} 90-99% error rate, severe degradation"
echo ""

clean_all
check_pods
echo ""

echo -e "${YELLOW}Starting crossfire attack (app-level)...${NC}"
cd attack-simulations
python3 crossfire-app-level.py \
    --url "$TARGET_URL" \
    --duration $DURATION \
    --rate $RATE \
    --workers $WORKERS \
    --targets discovered-endpoints.json \
    --non-interactive

echo ""
echo -e "${YELLOW}Running crossfire detection...${NC}"
python3 crossfire-detector.py \
    --url "$TARGET_URL" \
    --duration $DURATION

cd ..
wait_for_user

#==============================================================================
# SCENARIO 2: NATIVE KUBERNETES MITIGATION
#==============================================================================

echo -e "${MAGENTA}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${MAGENTA}   SCENARIO 2: NATIVE KUBERNETES MITIGATION${NC}"
echo -e "${MAGENTA}═══════════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "${CYAN}Purpose:${NC} Test standard K8s protections (HPAs, NetworkPolicies, ResourceQuotas)"
echo -e "${CYAN}Expected:${NC} 50-70% error rate, moderate protection"
echo ""

clean_all

echo -e "${YELLOW}Deploying native Kubernetes mitigations...${NC}"
kubectl apply -f mitigations/kubernetes-native/autoscaling/
kubectl apply -f mitigations/kubernetes-native/network-policies/
kubectl apply -f mitigations/kubernetes-native/resource-quotas/

echo ""
echo -e "${CYAN}Deployed resources:${NC}"
echo -e "  HPAs: $(kubectl get hpa -n $NAMESPACE 2>/dev/null | grep -v NAME | wc -l)"
echo -e "  NetworkPolicies: $(kubectl get networkpolicies -n $NAMESPACE 2>/dev/null | grep -v NAME | wc -l)"
echo -e "  ResourceQuotas: $(kubectl get resourcequotas -n $NAMESPACE 2>/dev/null | grep -v NAME | wc -l)"

echo ""
echo -e "${YELLOW}Waiting 30s for HPAs to stabilize...${NC}"
sleep 30
check_pods
echo ""

echo -e "${YELLOW}Starting crossfire attack (app-level)...${NC}"
cd attack-simulations
python3 crossfire-app-level.py \
    --url "$TARGET_URL" \
    --duration $DURATION \
    --rate $RATE \
    --workers $WORKERS \
    --targets discovered-endpoints.json \
    --non-interactive

echo ""
echo -e "${YELLOW}Running crossfire detection...${NC}"
python3 crossfire-detector.py \
    --url "$TARGET_URL" \
    --duration $DURATION

cd ..
wait_for_user

#==============================================================================
# SCENARIO 3: NEPHIO-ENHANCED MITIGATION
#==============================================================================

echo -e "${MAGENTA}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${MAGENTA}   SCENARIO 3: NEPHIO-ENHANCED MITIGATION${NC}"
echo -e "${MAGENTA}═══════════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "${CYAN}Purpose:${NC} Test intent-based mitigations with service classification"
echo -e "${CYAN}Expected:${NC} 30-50% error rate, enhanced protection"
echo ""

clean_all

echo -e "${YELLOW}Deploying Nephio-enhanced mitigations...${NC}"
cd mitigations/nephio
./deploy.sh
cd ../..

echo ""
echo -e "${CYAN}Nephio-managed resources:${NC}"
echo -e "  PriorityClasses: $(kubectl get priorityclasses -l nephio.org/managed=true 2>/dev/null | grep -v NAME | wc -l)"
echo -e "  HPAs: $(kubectl get hpa -n $NAMESPACE -l nephio.org/managed=true 2>/dev/null | grep -v NAME | wc -l)"
echo -e "  NetworkPolicies: $(kubectl get networkpolicies -n $NAMESPACE -l nephio.org/managed=true 2>/dev/null | grep -v NAME | wc -l)"
echo -e "  ResourceQuotas: $(kubectl get resourcequotas -n $NAMESPACE -l nephio.org/managed=true 2>/dev/null | grep -v NAME | wc -l)"

echo ""
echo -e "${CYAN}Capacity annotations:${NC}"
kubectl get hpa -n $NAMESPACE -l nephio.org/managed=true -o jsonpath='{range .items[*]}{"  "}{.metadata.name}{": "}{.metadata.annotations.nephio\.org/capacity-request}{"\n"}{end}'

echo ""
echo -e "${YELLOW}Waiting 30s for HPAs to stabilize...${NC}"
sleep 30
check_pods
echo ""

echo -e "${YELLOW}Starting crossfire attack (app-level)...${NC}"
cd attack-simulations
python3 crossfire-app-level.py \
    --url "$TARGET_URL" \
    --duration $DURATION \
    --rate $RATE \
    --workers $WORKERS \
    --targets discovered-endpoints.json \
    --non-interactive

echo ""
echo -e "${YELLOW}Running crossfire detection...${NC}"
python3 crossfire-detector.py \
    --url "$TARGET_URL" \
    --duration $DURATION

cd ..

#==============================================================================
# SUMMARY
#==============================================================================

echo ""
echo -e "${MAGENTA}╔══════════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${MAGENTA}║                     ALL SCENARIOS COMPLETE                           ║${NC}"
echo -e "${MAGENTA}╚══════════════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${CYAN}Results Summary:${NC}"
echo -e "  Scenario 1 (Baseline): Check output above for error rates"
echo -e "  Scenario 2 (Native K8s): Check output above for error rates"
echo -e "  Scenario 3 (Nephio): Check output above for error rates"
echo ""
echo -e "${GREEN}✓ Testing complete${NC}"
echo ""
echo -e "${CYAN}Compare the error rates and latencies across scenarios to see${NC}"
echo -e "${CYAN}the effectiveness of each mitigation approach.${NC}"
echo ""

# Cleanup port forwarding if we started it
if [ -n "$PORT_FORWARD_PID" ]; then
    echo -e "${YELLOW}Stopping port forwarding...${NC}"
    kill $PORT_FORWARD_PID 2>/dev/null || true
    echo -e "${GREEN}✓ Cleanup complete${NC}"
fi
