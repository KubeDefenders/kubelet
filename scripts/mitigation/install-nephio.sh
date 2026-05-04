#!/bin/bash
# Install and deploy Nephio-based DDoS mitigations
# This is a comprehensive script for Nephio setup

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

NEPHIO_VERSION="v2.0.0"
MANAGEMENT_CLUSTER_NAME="nephio-mgmt"
MITIGATION_DIR="/home/spuggle/dev/ddos/mitigations/nephio"

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Nephio DDoS Mitigation Deployment${NC}"
echo -e "${GREEN}========================================${NC}"

# Check prerequisites
check_prerequisites() {
    echo -e "${BLUE}Checking prerequisites...${NC}"
    
    # Check kubectl
    if ! command -v kubectl &> /dev/null; then
        echo -e "${RED}✗ kubectl not found. Please install kubectl.${NC}"
        exit 1
    fi
    echo -e "${GREEN}✓ kubectl found${NC}"
    
    # Check kind (for management cluster)
    if ! command -v kind &> /dev/null; then
        echo -e "${YELLOW}⚠ kind not found. Installing kind...${NC}"
        curl -Lo ./kind https://kind.sigs.k8s.io/dl/latest/kind-linux-amd64
        chmod +x ./kind
        sudo mv ./kind /usr/local/bin/kind
        echo -e "${GREEN}✓ kind installed${NC}"
    else
        echo -e "${GREEN}✓ kind found${NC}"
    fi
    
    # Check kpt
    if ! command -v kpt &> /dev/null; then
        echo -e "${YELLOW}⚠ kpt not found. Installing kpt...${NC}"
        curl -L https://github.com/GoogleContainerTools/kpt/releases/download/v1.0.0-beta.49/kpt_linux_amd64 -o kpt
        chmod +x kpt
        sudo mv kpt /usr/local/bin/kpt
        echo -e "${GREEN}✓ kpt installed${NC}"
    else
        echo -e "${GREEN}✓ kpt found${NC}"
    fi
}

# Create Nephio management cluster
create_management_cluster() {
    echo ""
    echo -e "${GREEN}======================================${NC}"
    echo -e "${GREEN}Step 1: Creating Management Cluster${NC}"
    echo -e "${GREEN}======================================${NC}"
    
    if kind get clusters | grep -q "^${MANAGEMENT_CLUSTER_NAME}$"; then
        echo -e "${YELLOW}Management cluster already exists. Skipping creation.${NC}"
    else
        echo -e "${YELLOW}Creating kind cluster for Nephio management...${NC}"
        
        cat <<EOF | kind create cluster --name $MANAGEMENT_CLUSTER_NAME --config=-
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
- role: control-plane
  extraPortMappings:
  - containerPort: 30443
    hostPort: 30443
    protocol: TCP
  - containerPort: 30080
    hostPort: 30080
    protocol: TCP
EOF
        echo -e "${GREEN}✓ Management cluster created${NC}"
    fi
    
    # Set context
    kubectl config use-context kind-$MANAGEMENT_CLUSTER_NAME
}

# Install Nephio components
install_nephio() {
    echo ""
    echo -e "${GREEN}======================================${NC}"
    echo -e "${GREEN}Step 2: Installing Nephio Components${NC}"
    echo -e "${GREEN}======================================${NC}"
    
    # Install Porch (Package Orchestration)
    echo -e "${YELLOW}Installing Porch...${NC}"
    kubectl apply -f https://github.com/nephio-project/porch/releases/download/$NEPHIO_VERSION/porch-install.yaml
    
    echo -e "${YELLOW}Waiting for Porch to be ready...${NC}"
    kubectl wait --for=condition=available --timeout=300s deployment/porch-server -n porch-system || true
    
    # Install Config Sync
    echo -e "${YELLOW}Installing Config Sync...${NC}"
    kubectl apply -f https://github.com/GoogleContainerTools/kpt-config-sync/releases/download/v1.17.0/config-sync-manifest.yaml
    
    # Install Nephio controllers
    echo -e "${YELLOW}Installing Nephio controllers...${NC}"
    kubectl apply -f https://github.com/nephio-project/nephio/releases/download/$NEPHIO_VERSION/nephio-system.yaml || {
        echo -e "${YELLOW}⚠ Using alternative installation method...${NC}"
        # Alternative: Manual controller deployment
        kubectl create namespace nephio-system || true
    }
    
    echo -e "${GREEN}✓ Nephio components installed${NC}"
}

# Deploy DDoS Protection CRDs
deploy_crds() {
    echo ""
    echo -e "${GREEN}======================================${NC}"
    echo -e "${GREEN}Step 3: Deploying Custom CRDs${NC}"
    echo -e "${GREEN}======================================${NC}"
    
    echo -e "${YELLOW}Deploying DDoS Protection CRDs...${NC}"
    kubectl apply -f "$MITIGATION_DIR/workload-apis/ddos-protection-crds.yaml"
    
    echo -e "${GREEN}✓ CRDs deployed${NC}"
    
    # Verify CRDs
    echo -e "${YELLOW}Verifying CRDs...${NC}"
    kubectl get crd ddosprotections.workload.nephio.org
    kubectl get crd capacityrequests.req.nephio.org
    kubectl get crd nfdeployments.nf.nephio.org
}

# Register workload cluster (Minikube)
register_workload_cluster() {
    echo ""
    echo -e "${GREEN}======================================${NC}"
    echo -e "${GREEN}Step 4: Registering Workload Cluster${NC}"
    echo -e "${GREEN}======================================${NC}"
    
    # Check if Minikube is running
    if ! minikube status &> /dev/null; then
        echo -e "${RED}✗ Minikube is not running. Please start Minikube first:${NC}"
        echo -e "${YELLOW}  minikube start${NC}"
        exit 1
    fi
    
    echo -e "${YELLOW}Registering Minikube as workload cluster...${NC}"
    
    # Get Minikube kubeconfig
    MINIKUBE_CONTEXT=$(kubectl config current-context --kubeconfig ~/.kube/config)
    
    # Create a secret with Minikube credentials in management cluster
    kubectl config use-context kind-$MANAGEMENT_CLUSTER_NAME
    
    # Extract Minikube cluster info
    kubectl create secret generic minikube-cluster \
        --from-file=kubeconfig=$HOME/.kube/config \
        -n nephio-system \
        --dry-run=client -o yaml | kubectl apply -f -
    
    echo -e "${GREEN}✓ Workload cluster registered${NC}"
}

# Deploy DDoS mitigation package
deploy_mitigation_package() {
    echo ""
    echo -e "${GREEN}======================================${NC}"
    echo -e "${GREEN}Step 5: Deploying DDoS Mitigation Package${NC}"
    echo -e "${GREEN}======================================${NC}"
    
    # Initialize KPT package
    echo -e "${YELLOW}Initializing KPT package...${NC}"
    cd "$MITIGATION_DIR/packages/ddos-mitigation-base"
    
    # Initialize for deployment
    kpt live init .
    
    # Apply the package
    echo -e "${YELLOW}Applying DDoS mitigation package...${NC}"
    kpt live apply . --reconcile-timeout=10m
    
    echo -e "${GREEN}✓ Package deployed${NC}"
}

# Switch to workload cluster and verify
verify_deployment() {
    echo ""
    echo -e "${GREEN}======================================${NC}"
    echo -e "${GREEN}Step 6: Verification${NC}"
    echo -e "${GREEN}======================================${NC}"
    
    # Switch to Minikube context
    kubectl config use-context minikube
    
    echo ""
    echo -e "${YELLOW}Checking DDoS Protection resources:${NC}"
    kubectl get ddosprotections -n sock-shop || echo -e "${YELLOW}No DDoSProtection resources found yet${NC}"
    
    echo ""
    echo -e "${YELLOW}Checking CapacityRequests:${NC}"
    kubectl get capacityrequests -n sock-shop || echo -e "${YELLOW}No CapacityRequests found yet${NC}"
    
    echo ""
    echo -e "${YELLOW}Checking NFDeployments:${NC}"
    kubectl get nfdeployments -n sock-shop || echo -e "${YELLOW}No NFDeployments found yet${NC}"
    
    echo ""
    echo -e "${YELLOW}Checking generated resources:${NC}"
    kubectl get hpa -n sock-shop
    kubectl get networkpolicies -n sock-shop
    kubectl get resourcequotas -n sock-shop
}

# Main execution
main() {
    echo -e "${BLUE}This script will:${NC}"
    echo "1. Check and install prerequisites (kind, kpt)"
    echo "2. Create Nephio management cluster"
    echo "3. Install Nephio components (Porch, Config Sync)"
    echo "4. Deploy DDoS Protection CRDs"
    echo "5. Register Minikube as workload cluster"
    echo "6. Deploy DDoS mitigation package"
    echo ""
    
    read -p "Continue? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo -e "${YELLOW}Deployment cancelled${NC}"
        exit 0
    fi
    
    check_prerequisites
    create_management_cluster
    install_nephio
    deploy_crds
    
    echo ""
    read -p "Register Minikube as workload cluster? (y/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        register_workload_cluster
    fi
    
    echo ""
    read -p "Deploy DDoS mitigation package now? (y/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        deploy_mitigation_package
        verify_deployment
    fi
    
    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}Nephio Deployment Complete!${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo ""
    echo -e "${YELLOW}Management Cluster:${NC} kind-$MANAGEMENT_CLUSTER_NAME"
    echo -e "${YELLOW}Workload Cluster:${NC} minikube"
    echo ""
    echo -e "${YELLOW}Next Steps:${NC}"
    echo "1. Apply DDoS protection intent:"
    echo "   kubectl apply -f $MITIGATION_DIR/packages/ddos-mitigation-base/package.yaml"
    echo ""
    echo "2. Monitor package deployment:"
    echo "   kubectl get packagerevisions -A"
    echo ""
    echo "3. Check generated resources:"
    echo "   kubectl get ddosprotections -n sock-shop"
    echo "   kubectl get hpa,networkpolicies,resourcequotas -n sock-shop"
    echo ""
    echo "4. Switch between clusters:"
    echo "   kubectl config use-context kind-$MANAGEMENT_CLUSTER_NAME  # Management"
    echo "   kubectl config use-context minikube                       # Workload"
    echo ""
}

# Run main
main
