#!/bin/bash
# Nephio-Enhanced DDoS Protection Deployment
# Applies Kubernetes-native resources with Nephio-style labels and annotations

set -e

# Configuration
TARGET_NAMESPACE="${TARGET_NAMESPACE:-sock-shop}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
NC='\033[0m'

echo -e "${MAGENTA}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${MAGENTA}     Deploying Nephio-Enhanced DDoS Protection${NC}"
echo -e "${MAGENTA}═══════════════════════════════════════════════════════════════${NC}"
echo ""

# Check prerequisites
if ! kubectl cluster-info &> /dev/null; then
    echo -e "${RED}✗ Cannot connect to Kubernetes cluster${NC}"
    exit 1
fi

# Ensure namespace exists
if ! kubectl get namespace "$TARGET_NAMESPACE" &> /dev/null; then
    echo -e "${YELLOW}Creating namespace: $TARGET_NAMESPACE${NC}"
    kubectl create namespace "$TARGET_NAMESPACE"
fi

# Label namespace
kubectl label namespace "$TARGET_NAMESPACE" \
    nephio.org/managed=true \
    nephio.org/protection-level=high \
    --overwrite &> /dev/null

echo -e "${CYAN}Target Namespace:${NC} $TARGET_NAMESPACE"
echo ""

# Deploy resources
echo -e "${YELLOW}[1/4] Deploying PriorityClasses...${NC}"
# PriorityClasses are immutable - delete and recreate if they exist
kubectl delete -f "$SCRIPT_DIR/translated/priority-classes.yaml" --ignore-not-found=true &> /dev/null
kubectl apply -f "$SCRIPT_DIR/translated/priority-classes.yaml"
COUNT=$(kubectl get priorityclasses -l nephio.org/managed=true 2>/dev/null | grep -v NAME | wc -l)
echo -e "${GREEN}✓ Deployed $COUNT PriorityClasses${NC}"
echo ""

echo -e "${YELLOW}[2/4] Deploying NetworkPolicies...${NC}"
kubectl apply -f "$SCRIPT_DIR/translated/network-policies.yaml"
COUNT=$(kubectl get networkpolicies -n "$TARGET_NAMESPACE" -l nephio.org/managed=true 2>/dev/null | grep -v NAME | wc -l)
echo -e "${GREEN}✓ Deployed $COUNT NetworkPolicies${NC}"
echo ""

echo -e "${YELLOW}[3/4] Deploying ResourceQuotas...${NC}"
kubectl apply -f "$SCRIPT_DIR/translated/resource-quotas.yaml"
COUNT=$(kubectl get resourcequotas -n "$TARGET_NAMESPACE" -l nephio.org/managed=true 2>/dev/null | grep -v NAME | wc -l)
echo -e "${GREEN}✓ Deployed $COUNT ResourceQuotas${NC}"
echo ""

echo -e "${YELLOW}[4/4] Deploying HorizontalPodAutoscalers...${NC}"
kubectl apply -f "$SCRIPT_DIR/translated/autoscaling.yaml"
COUNT=$(kubectl get hpa -n "$TARGET_NAMESPACE" -l nephio.org/managed=true 2>/dev/null | grep -v NAME | wc -l)
echo -e "${GREEN}✓ Deployed $COUNT HPAs${NC}"
echo ""

# Summary
echo -e "${MAGENTA}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${MAGENTA}                    Deployment Complete${NC}"
echo -e "${MAGENTA}═══════════════════════════════════════════════════════════════${NC}"
echo ""

echo -e "${CYAN}Deployed Nephio-Enhanced Resources:${NC}"
kubectl get priorityclasses -l nephio.org/managed=true 2>/dev/null | grep -v NAME | wc -l | xargs -I {} echo -e "  ${GREEN}{}${NC} PriorityClasses"
kubectl get networkpolicies -n "$TARGET_NAMESPACE" -l nephio.org/managed=true 2>/dev/null | grep -v NAME | wc -l | xargs -I {} echo -e "  ${GREEN}{}${NC} NetworkPolicies"
kubectl get resourcequotas -n "$TARGET_NAMESPACE" -l nephio.org/managed=true 2>/dev/null | grep -v NAME | wc -l | xargs -I {} echo -e "  ${GREEN}{}${NC} ResourceQuotas"
kubectl get hpa -n "$TARGET_NAMESPACE" -l nephio.org/managed=true 2>/dev/null | grep -v NAME | wc -l | xargs -I {} echo -e "  ${GREEN}{}${NC} HorizontalPodAutoscalers"
echo ""

echo -e "${CYAN}Key Nephio Features:${NC}"
echo -e "  ${MAGENTA}•${NC} Intent-based labels (nephio.org/intent=AutoScaling)"
echo -e "  ${MAGENTA}•${NC} Capacity annotations (nephio.org/capacity-request)"
echo -e "  ${MAGENTA}•${NC} Service classification (critical/normal/decoy)"
echo -e "  ${MAGENTA}•${NC} SLO annotations (nephio.org/slo-availability)"
echo -e "  ${MAGENTA}•${NC} Anti-crossfire network isolation"
echo ""

echo -e "${CYAN}Verification Commands:${NC}"
echo -e "  ${YELLOW}kubectl get hpa -n $TARGET_NAMESPACE -l nephio.org/managed=true${NC}"
echo -e "  ${YELLOW}kubectl get networkpolicies -n $TARGET_NAMESPACE -l nephio.org/managed=true${NC}"
echo -e "  ${YELLOW}kubectl describe hpa front-end-hpa-nephio -n $TARGET_NAMESPACE${NC}"
echo ""

echo -e "${GREEN}✓ Nephio-enhanced protection deployed successfully!${NC}"
