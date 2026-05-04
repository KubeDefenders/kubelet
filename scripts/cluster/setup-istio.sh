#!/bin/bash

# Istio Installation and Configuration Script
# Installs Istio service mesh and configures it for Sock Shop

set -e

ISTIO_VERSION="1.20.2"

echo "======================================"
echo "Installing Istio Service Mesh"
echo "======================================"
echo ""

# Check if kubectl is available
if ! command -v kubectl &> /dev/null; then
    echo "Error: kubectl is not installed"
    exit 1
fi

# Check if cluster is running
if ! kubectl cluster-info &> /dev/null; then
    echo "Error: Kubernetes cluster is not running"
    exit 1
fi

# Download and install Istio
if ! command -v istioctl &> /dev/null; then
    echo "[1/6] Downloading Istio ${ISTIO_VERSION}..."
    cd /tmp
    curl -L https://istio.io/downloadIstio | ISTIO_VERSION=${ISTIO_VERSION} sh -
    
    echo "Installing istioctl..."
    sudo mv istio-${ISTIO_VERSION}/bin/istioctl /usr/local/bin/
    chmod +x /usr/local/bin/istioctl
    
    # Keep samples and manifests
    if [ ! -d "$HOME/.istio" ]; then
        mkdir -p "$HOME/.istio"
    fi
    mv istio-${ISTIO_VERSION} "$HOME/.istio/"
    
    echo "istioctl installed: $(istioctl version --remote=false)"
else
    echo "[1/6] istioctl already installed: $(istioctl version --remote=false)"
fi

# Verify minimum Kubernetes version
echo "[2/6] Verifying Kubernetes version..."
if kubectl version --client --short >/dev/null 2>&1; then
    kubectl version --client --short
else
    kubectl version --client
fi

# Install Istio with demo profile (includes observability tools)
echo "[3/6] Installing Istio with demo profile..."
istioctl install --set profile=demo -y

# Verify installation
echo "[4/6] Verifying Istio installation..."
kubectl get pods -n istio-system

# Enable automatic sidecar injection for sock-shop namespace
echo "[5/6] Enabling automatic sidecar injection for sock-shop namespace..."
kubectl label namespace sock-shop istio-injection=enabled --overwrite

# Apply Sock Shop Istio configurations
echo "[6/6] Applying Istio configurations for Sock Shop..."
kubectl apply -f istio/gateway.yaml
kubectl apply -f istio/virtual-services.yaml
kubectl apply -f istio/destination-rules.yaml

echo ""
echo "======================================"
echo "Istio Installation Complete"
echo "======================================"
echo ""
echo "Istio components:"
kubectl get pods -n istio-system
echo ""
echo "Istio ingress gateway:"
kubectl get svc istio-ingressgateway -n istio-system
echo ""
echo "======================================"
echo "Next Steps"
echo "======================================"
echo ""
echo "1. If Sock Shop is already deployed, restart pods to inject sidecars:"
echo "   kubectl rollout restart deployment -n sock-shop"
echo ""
echo "2. Access Sock Shop via Istio ingress:"
echo "   export INGRESS_HOST=\$(minikube ip)"
echo "   export INGRESS_PORT=\$(kubectl -n istio-system get service istio-ingressgateway -o jsonpath='{.spec.ports[?(@.name==\"http2\")].nodePort}')"
echo "   echo \"http://\$INGRESS_HOST:\$INGRESS_PORT\""
echo ""
echo "3. Setup monitoring stack: ./scripts/setup-monitoring.sh"
echo ""
echo "4. See docs/03-istio-setup.md for more information"
