#!/bin/bash
# Verify Kubernetes Native Crossfire Mitigation Status

set -e

echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║     CROSSFIRE MITIGATION VERIFICATION                         ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""

NAMESPACE="${1:-sock-shop}"
echo "Checking namespace: $NAMESPACE"
echo ""

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Check function
check_resource() {
    local resource=$1
    local name=$2
    local namespace=$3
    
    if kubectl get $resource $name -n $namespace &>/dev/null; then
        echo -e "${GREEN}✅${NC} $resource/$name exists"
        return 0
    else
        echo -e "${RED}❌${NC} $resource/$name not found"
        return 1
    fi
}

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "1. NETWORK POLICIES (Traffic Isolation)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

NETPOL_COUNT=$(kubectl get networkpolicies -n $NAMESPACE --no-headers 2>/dev/null | wc -l)
if [ $NETPOL_COUNT -gt 0 ]; then
    echo -e "${GREEN}✅${NC} Found $NETPOL_COUNT NetworkPolicies"
    echo ""
    kubectl get networkpolicies -n $NAMESPACE
    echo ""
    
    # Check crossfire-specific policies
    if kubectl get networkpolicy anti-crossfire-decoy-isolation -n $NAMESPACE &>/dev/null; then
        echo -e "${GREEN}✅ Crossfire decoy isolation policy exists${NC}"
    else
        echo -e "${YELLOW}⚠️  Crossfire decoy isolation policy not found${NC}"
    fi
    
    if kubectl get networkpolicy anti-crossfire-critical-isolation -n $NAMESPACE &>/dev/null; then
        echo -e "${GREEN}✅ Crossfire critical service protection exists${NC}"
    else
        echo -e "${YELLOW}⚠️  Crossfire critical service protection not found${NC}"
    fi
else
    echo -e "${RED}❌ No NetworkPolicies found${NC}"
    echo "   Deploy with: kubectl apply -f mitigations/kubernetes-native/network-policies/"
fi
echo ""

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "2. RESOURCE QUOTAS (Resource Protection)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

QUOTA_COUNT=$(kubectl get resourcequota -n $NAMESPACE --no-headers 2>/dev/null | wc -l)
if [ $QUOTA_COUNT -gt 0 ]; then
    echo -e "${GREEN}✅${NC} Found $QUOTA_COUNT ResourceQuotas"
    echo ""
    kubectl get resourcequota -n $NAMESPACE
    echo ""
    
    # Show quota usage
    echo "Quota Usage:"
    kubectl describe resourcequota -n $NAMESPACE | grep -A 10 "Used\|Hard" | head -20
else
    echo -e "${RED}❌ No ResourceQuotas found${NC}"
    echo "   Deploy with: kubectl apply -f mitigations/kubernetes-native/resource-quotas/"
fi
echo ""

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "3. PRIORITY CLASSES (Service Prioritization)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if kubectl get priorityclass critical-priority &>/dev/null; then
    echo -e "${GREEN}✅ critical-priority PriorityClass exists${NC}"
else
    echo -e "${YELLOW}⚠️  critical-priority PriorityClass not found${NC}"
fi

if kubectl get priorityclass decoy-priority &>/dev/null; then
    echo -e "${GREEN}✅ decoy-priority PriorityClass exists${NC}"
else
    echo -e "${YELLOW}⚠️  decoy-priority PriorityClass not found${NC}"
fi

echo ""
echo "Pod Priority Assignments:"
kubectl get pods -n $NAMESPACE -o custom-columns=NAME:.metadata.name,PRIORITY:.spec.priorityClassName 2>/dev/null | head -10
echo ""

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "4. SERVICE LABELS (Decoy vs Critical)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

echo "Decoy Services (should be isolated):"
DECOY_COUNT=$(kubectl get pods -n $NAMESPACE -l decoy-service=true --no-headers 2>/dev/null | wc -l)
if [ $DECOY_COUNT -gt 0 ]; then
    echo -e "${GREEN}✅ Found $DECOY_COUNT decoy service pods${NC}"
    kubectl get pods -n $NAMESPACE -l decoy-service=true
else
    echo -e "${YELLOW}⚠️  No pods labeled as decoy-service=true${NC}"
    echo "   Add labels: kubectl label pods -l app=catalogue decoy-service=true -n $NAMESPACE"
fi
echo ""

echo "Critical Services (should be protected):"
CRITICAL_COUNT=$(kubectl get pods -n $NAMESPACE -l critical-service=true --no-headers 2>/dev/null | wc -l)
if [ $CRITICAL_COUNT -gt 0 ]; then
    echo -e "${GREEN}✅ Found $CRITICAL_COUNT critical service pods${NC}"
    kubectl get pods -n $NAMESPACE -l critical-service=true
else
    echo -e "${YELLOW}⚠️  No pods labeled as critical-service=true${NC}"
    echo "   Add labels: kubectl label pods -l app=payment critical-service=true -n $NAMESPACE"
fi
echo ""

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "5. HORIZONTAL POD AUTOSCALING"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

HPA_COUNT=$(kubectl get hpa -n $NAMESPACE --no-headers 2>/dev/null | wc -l)
if [ $HPA_COUNT -gt 0 ]; then
    echo -e "${GREEN}✅${NC} Found $HPA_COUNT HorizontalPodAutoscalers"
    echo ""
    kubectl get hpa -n $NAMESPACE
else
    echo -e "${RED}❌ No HorizontalPodAutoscalers found${NC}"
    echo "   Deploy with: kubectl apply -f mitigations/kubernetes-native/autoscaling/"
fi
echo ""

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "6. POD DISRUPTION BUDGETS"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

PDB_COUNT=$(kubectl get pdb -n $NAMESPACE --no-headers 2>/dev/null | wc -l)
if [ $PDB_COUNT -gt 0 ]; then
    echo -e "${GREEN}✅${NC} Found $PDB_COUNT PodDisruptionBudgets"
    echo ""
    kubectl get pdb -n $NAMESPACE
else
    echo -e "${RED}❌ No PodDisruptionBudgets found${NC}"
    echo "   Deploy with: kubectl apply -f mitigations/kubernetes-native/pod-disruption/"
fi
echo ""

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "SUMMARY"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

TOTAL_CHECKS=6
PASSED=0

[ $NETPOL_COUNT -gt 0 ] && ((PASSED++))
[ $QUOTA_COUNT -gt 0 ] && ((PASSED++))
kubectl get priorityclass critical-priority &>/dev/null && ((PASSED++))
[ $DECOY_COUNT -gt 0 ] && ((PASSED++))
[ $HPA_COUNT -gt 0 ] && ((PASSED++))
[ $PDB_COUNT -gt 0 ] && ((PASSED++))

echo "Mitigation Coverage: $PASSED/$TOTAL_CHECKS"
echo ""

if [ $PASSED -eq $TOTAL_CHECKS ]; then
    echo -e "${GREEN}✅ All crossfire mitigations deployed!${NC}"
elif [ $PASSED -ge 4 ]; then
    echo -e "${YELLOW}⚠️  Partial mitigation coverage - some protections missing${NC}"
else
    echo -e "${RED}❌ Insufficient mitigation coverage${NC}"
fi
echo ""

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "NEXT STEPS"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Deploy missing mitigations:"
echo "  ./mitigations/scripts/deploy-native-baseline.sh"
echo ""
echo "Test crossfire protection:"
echo "  cd attack-simulations"
echo "  python crossfire-app-level.py --target-url http://192.168.49.2:30001"
echo ""
echo "Monitor during attack:"
echo "  watch -n 2 'kubectl get hpa,pods -n $NAMESPACE'"
echo ""
echo "Verify network isolation:"
echo "  kubectl exec -n $NAMESPACE <catalogue-pod> -- curl payment"
echo "  (Should fail if network policies are working)"
echo ""
