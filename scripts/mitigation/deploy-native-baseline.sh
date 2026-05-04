#!/bin/bash
# Deploy Native Kubernetes baseline DDoS mitigations
# This script applies NetworkPolicies, ResourceQuotas, Autoscaling, and PDB configurations

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
NAMESPACE="sock-shop"
MITIGATION_DIR="/home/spuggle/dev/ddos/mitigation/kubernetes-native"

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Native Kubernetes DDoS Mitigation Deployment${NC}"
echo -e "${GREEN}========================================${NC}"

# Check if kubectl is available
if ! command -v kubectl &> /dev/null; then
    echo -e "${RED}Error: kubectl not found. Please install kubectl.${NC}"
    exit 1
fi

# Check if cluster is accessible
if ! kubectl cluster-info &> /dev/null; then
    echo -e "${RED}Error: Cannot connect to Kubernetes cluster.${NC}"
    echo -e "${YELLOW}Hint: Is Minikube running? Try: minikube start${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Kubernetes cluster is accessible${NC}"

# Check if namespace exists
if ! kubectl get namespace $NAMESPACE &> /dev/null; then
    echo -e "${YELLOW}Namespace $NAMESPACE does not exist. Creating...${NC}"
    kubectl create namespace $NAMESPACE
    echo -e "${GREEN}✓ Namespace created${NC}"
else
    echo -e "${GREEN}✓ Namespace $NAMESPACE exists${NC}"
fi

# Function to apply configurations with error handling
apply_config() {
    local config_path=$1
    local description=$2
    
    echo ""
    echo -e "${YELLOW}Deploying: $description${NC}"
    
    if [ -f "$config_path" ]; then
        if kubectl apply -f "$config_path"; then
            echo -e "${GREEN}✓ $description applied successfully${NC}"
        else
            echo -e "${RED}✗ Failed to apply $description${NC}"
            return 1
        fi
    else
        echo -e "${RED}✗ File not found: $config_path${NC}"
        return 1
    fi
}

# 1. Deploy PriorityClasses (must be done first, cluster-scoped)
echo ""
echo -e "${GREEN}======================================${NC}"
echo -e "${GREEN}Step 1: Deploying PriorityClasses${NC}"
echo -e "${GREEN}======================================${NC}"
apply_config "$MITIGATION_DIR/resource-quotas/priority-classes.yaml" "PriorityClasses (critical, normal, decoy)"

# 2. Deploy NetworkPolicies
echo ""
echo -e "${GREEN}======================================${NC}"
echo -e "${GREEN}Step 2: Deploying NetworkPolicies${NC}"
echo -e "${GREEN}======================================${NC}"
apply_config "$MITIGATION_DIR/network-policies/00-default-deny.yaml" "Default Deny NetworkPolicy"
apply_config "$MITIGATION_DIR/network-policies/01-frontend-isolation.yaml" "Frontend Isolation NetworkPolicy"
apply_config "$MITIGATION_DIR/network-policies/02-crossfire-protection.yaml" "Crossfire Protection NetworkPolicy"

# 3. Deploy ResourceQuotas and LimitRanges
echo ""
echo -e "${GREEN}======================================${NC}"
echo -e "${GREEN}Step 3: Deploying Resource Quotas${NC}"
echo -e "${GREEN}======================================${NC}"
apply_config "$MITIGATION_DIR/resource-quotas/namespace-quotas.yaml" "Namespace ResourceQuotas"
apply_config "$MITIGATION_DIR/resource-quotas/limit-ranges.yaml" "LimitRanges"

# 4. Label services for Crossfire protection
echo ""
echo -e "${GREEN}======================================${NC}"
echo -e "${GREEN}Step 4: Labeling Services${NC}"
echo -e "${GREEN}======================================${NC}"

echo -e "${YELLOW}Labeling decoy services...${NC}"
for service in catalogue carts user; do
    if kubectl get deployment $service -n $NAMESPACE &> /dev/null; then
        kubectl label deployment $service -n $NAMESPACE decoy-service=true --overwrite
        echo -e "${GREEN}✓ Labeled $service as decoy${NC}"
    else
        echo -e "${YELLOW}⚠ Deployment $service not found (may not be deployed yet)${NC}"
    fi
done

echo -e "${YELLOW}Labeling critical services...${NC}"
for service in payment orders shipping; do
    if kubectl get deployment $service -n $NAMESPACE &> /dev/null; then
        kubectl label deployment $service -n $NAMESPACE critical-service=true --overwrite
        echo -e "${GREEN}✓ Labeled $service as critical${NC}"
    else
        echo -e "${YELLOW}⚠ Deployment $service not found (may not be deployed yet)${NC}"
    fi
done

# 5. Deploy Autoscaling (HPA)
echo ""
echo -e "${GREEN}======================================${NC}"
echo -e "${GREEN}Step 5: Deploying Autoscaling (HPA)${NC}"
echo -e "${GREEN}======================================${NC}"

# Check if metrics-server is installed
if ! kubectl get deployment metrics-server -n kube-system &> /dev/null; then
    echo -e "${YELLOW}⚠ metrics-server not found. HPA requires metrics-server.${NC}"
    echo -e "${YELLOW}Installing metrics-server...${NC}"
    kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
    
    # Patch metrics-server for Minikube (insecure)
    kubectl patch deployment metrics-server -n kube-system --type='json' \
        -p='[{"op": "add", "path": "/spec/template/spec/containers/0/args/-", "value": "--kubelet-insecure-tls"}]'
    
    echo -e "${YELLOW}Waiting for metrics-server to be ready...${NC}"
    kubectl wait --for=condition=available --timeout=60s deployment/metrics-server -n kube-system
    
    # Wait for metrics to be available
    echo -e "${YELLOW}Waiting for metrics to populate (30 seconds)...${NC}"
    sleep 30
fi

apply_config "$MITIGATION_DIR/autoscaling/hpa-configurations.yaml" "HorizontalPodAutoscalers"

# 6. Deploy VPA (optional, requires VPA controller)
echo ""
echo -e "${GREEN}======================================${NC}"
echo -e "${GREEN}Step 6: Deploying VPA (optional)${NC}"
echo -e "${GREEN}======================================${NC}"

if kubectl get crd verticalpodautoscalers.autoscaling.k8s.io &> /dev/null; then
    apply_config "$MITIGATION_DIR/autoscaling/vpa-configurations.yaml" "VerticalPodAutoscalers"
else
    echo -e "${YELLOW}⚠ VPA CRD not found. Skipping VPA deployment.${NC}"
    echo -e "${YELLOW}  To install VPA: https://github.com/kubernetes/autoscaler/tree/master/vertical-pod-autoscaler${NC}"
fi

# 7. Deploy PodDisruptionBudgets
echo ""
echo -e "${GREEN}======================================${NC}"
echo -e "${GREEN}Step 7: Deploying PodDisruptionBudgets${NC}"
echo -e "${GREEN}======================================${NC}"
apply_config "$MITIGATION_DIR/pod-disruption/pdb-configurations.yaml" "PodDisruptionBudgets"

# Verification
echo ""
echo -e "${GREEN}======================================${NC}"
echo -e "${GREEN}Verification${NC}"
echo -e "${GREEN}======================================${NC}"

echo ""
echo -e "${YELLOW}Checking NetworkPolicies:${NC}"
kubectl get networkpolicies -n $NAMESPACE

echo ""
echo -e "${YELLOW}Checking ResourceQuotas:${NC}"
kubectl get resourcequotas -n $NAMESPACE

echo ""
echo -e "${YELLOW}Checking LimitRanges:${NC}"
kubectl get limitranges -n $NAMESPACE

echo ""
echo -e "${YELLOW}Checking PriorityClasses:${NC}"
kubectl get priorityclasses

echo ""
echo -e "${YELLOW}Checking HPAs:${NC}"
kubectl get hpa -n $NAMESPACE

echo ""
echo -e "${YELLOW}Checking PDBs:${NC}"
kubectl get pdb -n $NAMESPACE

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Deployment Complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "${YELLOW}Next Steps:${NC}"
echo "1. Deploy sock-shop application if not already deployed"
echo "2. Verify mitigations are working:"
echo "   kubectl describe resourcequota -n $NAMESPACE"
echo "   kubectl describe hpa -n $NAMESPACE"
echo "3. Deploy Istio advanced mitigations (optional):"
echo "   ./mitigations/scripts/deploy-istio-advanced.sh"
echo "4. Test against DDoS attacks:"
echo "   python attack-simulations/crossfire-app-level.py"
echo ""
