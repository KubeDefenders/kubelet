#!/bin/bash
# Deployment script for Nephio Crossfire Protection Package
# This script deploys the complete package to target clusters

set -e

# Configuration
PACKAGE_NAME="crossfire-protection-package"
PACKAGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_NAMESPACE="${TARGET_NAMESPACE:-sock-shop}"
CLUSTER_NAME="${CLUSTER_NAME:-edge-cluster-01}"
PROTECTION_LEVEL="${PROTECTION_LEVEL:-high}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}╔═══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║     Nephio Crossfire Protection Package Deployment           ║${NC}"
echo -e "${BLUE}╚═══════════════════════════════════════════════════════════════╝${NC}"
echo ""

# Check prerequisites
echo -e "${YELLOW}Checking prerequisites...${NC}"

if ! command -v kubectl &> /dev/null; then
    echo -e "${RED}Error: kubectl not found${NC}"
    exit 1
fi

if ! command -v kpt &> /dev/null; then
    echo -e "${YELLOW}Warning: kpt not found, some features may not work${NC}"
    KPT_AVAILABLE=false
else
    KPT_AVAILABLE=true
fi

# Check if namespace exists
if ! kubectl get namespace "$TARGET_NAMESPACE" &> /dev/null; then
    echo -e "${YELLOW}Creating namespace: $TARGET_NAMESPACE${NC}"
    kubectl create namespace "$TARGET_NAMESPACE"
fi

# Label namespace for Nephio management
kubectl label namespace "$TARGET_NAMESPACE" \
    nephio.org/managed=true \
    nephio.org/package="$PACKAGE_NAME" \
    nephio.org/protection-level="$PROTECTION_LEVEL" \
    --overwrite

echo -e "${GREEN}✓ Prerequisites checked${NC}"
echo ""

# Deploy Custom Resource Definitions
echo -e "${YELLOW}Deploying Custom Resource Definitions...${NC}"
if [ -f "$PACKAGE_DIR/../../workload-apis/ddos-protection-crds.yaml" ]; then
    kubectl apply -f "$PACKAGE_DIR/../../workload-apis/ddos-protection-crds.yaml"
    echo -e "${GREEN}✓ CRDs deployed${NC}"
else
    echo -e "${YELLOW}Warning: CRDs file not found, skipping${NC}"
fi
echo ""

# Process package with kpt if available
if [ "$KPT_AVAILABLE" = true ]; then
    echo -e "${YELLOW}Processing package with kpt...${NC}"
    
    # Set package context
    kpt fn eval "$PACKAGE_DIR" \
        --image gcr.io/kpt-fn/set-annotations:v0.1.4 -- \
        target-namespace="$TARGET_NAMESPACE" \
        cluster-name="$CLUSTER_NAME" \
        protection-level="$PROTECTION_LEVEL"
    
    # Run kpt functions
    kpt fn render "$PACKAGE_DIR"
    
    echo -e "${GREEN}✓ Package processed${NC}"
fi
echo ""

# Deploy components in order
echo -e "${YELLOW}Deploying components...${NC}"

# 1. Resource Quotas
echo -e "  ${BLUE}→${NC} Deploying resource quotas..."
kubectl apply -f "$PACKAGE_DIR/resource-quotas.yaml"
sleep 2

# 2. Network Policies
echo -e "  ${BLUE}→${NC} Deploying network policies..."
kubectl apply -f "$PACKAGE_DIR/network-policies.yaml"
sleep 2

# 3. Rate Limiting
echo -e "  ${BLUE}→${NC} Deploying rate limiting..."
kubectl apply -f "$PACKAGE_DIR/rate-limiting.yaml"
sleep 2

# 4. Autoscaling
echo -e "  ${BLUE}→${NC} Deploying autoscaling configurations..."
kubectl apply -f "$PACKAGE_DIR/autoscaling.yaml"
sleep 2

# 5. Traffic Management
echo -e "  ${BLUE}→${NC} Deploying traffic management..."
kubectl apply -f "$PACKAGE_DIR/traffic-management.yaml"
sleep 2

# 6. Monitoring
echo -e "  ${BLUE}→${NC} Deploying monitoring..."
kubectl apply -f "$PACKAGE_DIR/monitoring.yaml"
sleep 2

echo -e "${GREEN}✓ All components deployed${NC}"
echo ""

# Verify deployment
echo -e "${YELLOW}Verifying deployment...${NC}"

# Check network policies
NP_COUNT=$(kubectl get networkpolicies -n "$TARGET_NAMESPACE" -l nephio.org/managed=true --no-headers 2>/dev/null | wc -l)
echo -e "  ${BLUE}→${NC} Network Policies: $NP_COUNT"

# Check HPAs
HPA_COUNT=$(kubectl get hpa -n "$TARGET_NAMESPACE" --no-headers 2>/dev/null | wc -l)
echo -e "  ${BLUE}→${NC} Horizontal Pod Autoscalers: $HPA_COUNT"

# Check resource quotas
RQ_COUNT=$(kubectl get resourcequotas -n "$TARGET_NAMESPACE" --no-headers 2>/dev/null | wc -l)
echo -e "  ${BLUE}→${NC} Resource Quotas: $RQ_COUNT"

# Check custom resources
if kubectl get ddosprotections -n "$TARGET_NAMESPACE" &> /dev/null; then
    DDOS_COUNT=$(kubectl get ddosprotections -n "$TARGET_NAMESPACE" --no-headers 2>/dev/null | wc -l)
    echo -e "  ${BLUE}→${NC} DDoS Protection Resources: $DDOS_COUNT"
fi

echo ""
echo -e "${GREEN}✓ Deployment verification complete${NC}"
echo ""

# Display summary
echo -e "${BLUE}╔═══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║     Deployment Summary                                        ║${NC}"
echo -e "${BLUE}╚═══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Package: ${GREEN}$PACKAGE_NAME${NC}"
echo -e "  Namespace: ${GREEN}$TARGET_NAMESPACE${NC}"
echo -e "  Cluster: ${GREEN}$CLUSTER_NAME${NC}"
echo -e "  Protection Level: ${GREEN}$PROTECTION_LEVEL${NC}"
echo ""
echo -e "${YELLOW}Next Steps:${NC}"
echo ""
echo "  1. Verify service health:"
echo "     kubectl get pods -n $TARGET_NAMESPACE"
echo ""
echo "  2. Check HPA status:"
echo "     kubectl get hpa -n $TARGET_NAMESPACE"
echo ""
echo "  3. Monitor attack detection:"
echo "     kubectl logs -n $TARGET_NAMESPACE -l app=ml-detector -f"
echo ""
echo "  4. View metrics dashboard:"
echo "     kubectl port-forward -n $TARGET_NAMESPACE svc/grafana 3000:3000"
echo ""
echo "  5. Test attack mitigation:"
echo "     cd attack-simulations && ./enhanced-attacks.sh"
echo ""
echo -e "${GREEN}Deployment complete!${NC}"
