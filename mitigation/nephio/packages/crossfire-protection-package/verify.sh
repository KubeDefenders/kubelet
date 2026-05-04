#!/bin/bash
# Verification script for Nephio Crossfire Protection
# Checks if all mitigations are properly deployed and functioning

set -e

TARGET_NAMESPACE="${TARGET_NAMESPACE:-sock-shop}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}╔═══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║     Nephio Crossfire Protection Verification                  ║${NC}"
echo -e "${BLUE}╚═══════════════════════════════════════════════════════════════╝${NC}"
echo ""

CHECKS_PASSED=0
CHECKS_FAILED=0
CHECKS_WARNING=0

check_status() {
    local name=$1
    local status=$2
    
    if [ "$status" = "PASS" ]; then
        echo -e "  ${GREEN}✓${NC} $name"
        ((CHECKS_PASSED++))
    elif [ "$status" = "WARN" ]; then
        echo -e "  ${YELLOW}⚠${NC} $name"
        ((CHECKS_WARNING++))
    else
        echo -e "  ${RED}✗${NC} $name"
        ((CHECKS_FAILED++))
    fi
}

# 1. Check Kubernetes Native Mitigations
echo -e "${YELLOW}1. Kubernetes Native Mitigations${NC}"

# Network Policies
if kubectl get networkpolicies -n "$TARGET_NAMESPACE" | grep -q "anti-crossfire"; then
    NP_COUNT=$(kubectl get networkpolicies -n "$TARGET_NAMESPACE" | grep "anti-crossfire" | wc -l)
    if [ "$NP_COUNT" -ge 2 ]; then
        check_status "Network Policies (Decoy & Critical Isolation)" "PASS"
    else
        check_status "Network Policies (Incomplete: $NP_COUNT/2)" "WARN"
    fi
else
    check_status "Network Policies" "FAIL"
fi

# Horizontal Pod Autoscalers
if kubectl get hpa -n "$TARGET_NAMESPACE" &> /dev/null; then
    HPA_COUNT=$(kubectl get hpa -n "$TARGET_NAMESPACE" --no-headers | wc -l)
    if [ "$HPA_COUNT" -ge 2 ]; then
        check_status "Horizontal Pod Autoscalers ($HPA_COUNT configured)" "PASS"
    else
        check_status "Horizontal Pod Autoscalers (Only $HPA_COUNT)" "WARN"
    fi
else
    check_status "Horizontal Pod Autoscalers" "FAIL"
fi

# Resource Quotas
if kubectl get resourcequotas -n "$TARGET_NAMESPACE" &> /dev/null; then
    RQ_COUNT=$(kubectl get resourcequotas -n "$TARGET_NAMESPACE" --no-headers | wc -l)
    if [ "$RQ_COUNT" -ge 1 ]; then
        check_status "Resource Quotas ($RQ_COUNT configured)" "PASS"
    else
        check_status "Resource Quotas" "FAIL"
    fi
else
    check_status "Resource Quotas" "FAIL"
fi

# Priority Classes
if kubectl get priorityclasses | grep -qE "critical-priority|decoy-priority"; then
    check_status "Priority Classes (Critical & Decoy)" "PASS"
else
    check_status "Priority Classes" "WARN"
fi

# Pod Disruption Budgets
if kubectl get pdb -n "$TARGET_NAMESPACE" &> /dev/null; then
    PDB_COUNT=$(kubectl get pdb -n "$TARGET_NAMESPACE" --no-headers | wc -l)
    if [ "$PDB_COUNT" -ge 1 ]; then
        check_status "Pod Disruption Budgets ($PDB_COUNT configured)" "PASS"
    else
        check_status "Pod Disruption Budgets" "WARN"
    fi
else
    check_status "Pod Disruption Budgets" "WARN"
fi

echo ""

# 2. Check Istio Mitigations
echo -e "${YELLOW}2. Istio Service Mesh Mitigations${NC}"

# Check if Istio is installed
if kubectl get namespace istio-system &> /dev/null; then
    check_status "Istio Installed" "PASS"
    
    # Virtual Services
    if kubectl get virtualservices -n "$TARGET_NAMESPACE" &> /dev/null; then
        VS_COUNT=$(kubectl get virtualservices -n "$TARGET_NAMESPACE" --no-headers | wc -l)
        if [ "$VS_COUNT" -ge 1 ]; then
            check_status "Virtual Services ($VS_COUNT configured)" "PASS"
        else
            check_status "Virtual Services" "WARN"
        fi
    else
        check_status "Virtual Services" "WARN"
    fi
    
    # Destination Rules (Circuit Breakers)
    if kubectl get destinationrules -n "$TARGET_NAMESPACE" &> /dev/null; then
        DR_COUNT=$(kubectl get destinationrules -n "$TARGET_NAMESPACE" --no-headers | wc -l)
        if [ "$DR_COUNT" -ge 1 ]; then
            check_status "Destination Rules / Circuit Breakers ($DR_COUNT)" "PASS"
        else
            check_status "Destination Rules / Circuit Breakers" "WARN"
        fi
    else
        check_status "Destination Rules / Circuit Breakers" "WARN"
    fi
    
    # Rate Limiting
    if kubectl get envoyfilters -n istio-system | grep -q "rate-limit"; then
        check_status "Istio Rate Limiting (EnvoyFilter)" "PASS"
    else
        check_status "Istio Rate Limiting" "WARN"
    fi
else
    check_status "Istio Not Installed" "WARN"
fi

echo ""

# 3. Check Nephio-Exclusive Features
echo -e "${YELLOW}3. Nephio-Exclusive Features${NC}"

# Check if Nephio CRDs are installed
if kubectl get crd ddosprotections.workload.nephio.org &> /dev/null; then
    check_status "Nephio CRDs Installed" "PASS"
    
    # DDoS Protection Resources
    if kubectl get ddosprotections -n "$TARGET_NAMESPACE" &> /dev/null 2>&1; then
        DDOS_COUNT=$(kubectl get ddosprotections -n "$TARGET_NAMESPACE" --no-headers 2>/dev/null | wc -l)
        if [ "$DDOS_COUNT" -ge 1 ]; then
            check_status "DDoS Protection Resources ($DDOS_COUNT)" "PASS"
        else
            check_status "DDoS Protection Resources" "WARN"
        fi
    else
        check_status "DDoS Protection Resources" "WARN"
    fi
    
    # Dynamic Network Policies
    if kubectl get dynamicnetworkpolicies -n "$TARGET_NAMESPACE" &> /dev/null 2>&1; then
        check_status "Dynamic Network Policies (Attack-Adaptive)" "PASS"
    else
        check_status "Dynamic Network Policies" "WARN"
    fi
    
    # Capacity Requests
    if kubectl get capacityrequests -n "$TARGET_NAMESPACE" &> /dev/null 2>&1; then
        check_status "Multi-Cluster Capacity Coordination" "PASS"
    else
        check_status "Multi-Cluster Capacity Coordination" "WARN"
    fi
    
    # Predictive Autoscaling
    if kubectl get predictiveautoscaling -n "$TARGET_NAMESPACE" &> /dev/null 2>&1; then
        check_status "Predictive Autoscaling (ML-Based)" "PASS"
    else
        check_status "Predictive Autoscaling" "WARN"
    fi
    
    # Network Function Chains
    if kubectl get networkfunctionchains -n "$TARGET_NAMESPACE" &> /dev/null 2>&1; then
        check_status "Network Function Chaining" "PASS"
    else
        check_status "Network Function Chaining" "WARN"
    fi
    
    # Dynamic Traffic Steering
    if kubectl get dynamictrafficsteering -n "$TARGET_NAMESPACE" &> /dev/null 2>&1; then
        check_status "Dynamic Traffic Steering" "PASS"
    else
        check_status "Dynamic Traffic Steering" "WARN"
    fi
    
    # Multi-Cluster Distribution
    if kubectl get multiclustertrafficdistribution -n "$TARGET_NAMESPACE" &> /dev/null 2>&1; then
        check_status "Multi-Cluster Traffic Distribution" "PASS"
    else
        check_status "Multi-Cluster Traffic Distribution" "WARN"
    fi
else
    check_status "Nephio CRDs Not Installed" "FAIL"
    check_status "Nephio-exclusive features unavailable" "WARN"
fi

echo ""

# 4. Check Monitoring
echo -e "${YELLOW}4. Monitoring & Observability${NC}"

# Prometheus
if kubectl get servicemonitors -n "$TARGET_NAMESPACE" &> /dev/null 2>&1; then
    check_status "Prometheus ServiceMonitors" "PASS"
else
    check_status "Prometheus ServiceMonitors" "WARN"
fi

# Prometheus Rules
if kubectl get prometheusrules -n "$TARGET_NAMESPACE" &> /dev/null 2>&1; then
    check_status "Prometheus Alert Rules" "PASS"
else
    check_status "Prometheus Alert Rules" "WARN"
fi

# ML Detector
if kubectl get pods -n "$TARGET_NAMESPACE" -l app=ml-detector &> /dev/null; then
    ML_PODS=$(kubectl get pods -n "$TARGET_NAMESPACE" -l app=ml-detector --field-selector=status.phase=Running --no-headers 2>/dev/null | wc -l)
    if [ "$ML_PODS" -ge 1 ]; then
        check_status "ML Detector Running ($ML_PODS pods)" "PASS"
    else
        check_status "ML Detector Not Running" "WARN"
    fi
else
    check_status "ML Detector Not Deployed" "WARN"
fi

# Telemetry Aggregation
if kubectl get telemetryaggregation -n nephio-system &> /dev/null 2>&1; then
    check_status "Multi-Cluster Telemetry Aggregation" "PASS"
else
    check_status "Multi-Cluster Telemetry Aggregation" "WARN"
fi

echo ""

# Summary
echo -e "${BLUE}╔═══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║     Verification Summary                                      ║${NC}"
echo -e "${BLUE}╚═══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${GREEN}Passed:${NC}  $CHECKS_PASSED"
echo -e "  ${YELLOW}Warnings:${NC} $CHECKS_WARNING"
echo -e "  ${RED}Failed:${NC}  $CHECKS_FAILED"
echo ""

TOTAL_CHECKS=$((CHECKS_PASSED + CHECKS_WARNING + CHECKS_FAILED))
COVERAGE=$((CHECKS_PASSED * 100 / TOTAL_CHECKS))

echo -e "  Coverage: ${COVERAGE}%"
echo ""

if [ "$CHECKS_FAILED" -eq 0 ]; then
    if [ "$CHECKS_WARNING" -eq 0 ]; then
        echo -e "${GREEN}✓ All checks passed! Protection is fully deployed.${NC}"
        exit 0
    else
        echo -e "${YELLOW}⚠ Basic protection deployed, some features missing.${NC}"
        exit 0
    fi
else
    echo -e "${RED}✗ Some critical checks failed. Review configuration.${NC}"
    exit 1
fi
