#!/bin/bash

# Minikube Setup Script for Crossfire DDoS Simulation
# This script installs and configures Minikube, kubectl, and related dependencies

set -e

MINIKUBE_VERSION="latest"
KUBECTL_VERSION="$(curl -L -s https://dl.k8s.io/release/stable.txt)"

echo "======================================"
echo "Crossfire DDoS Simulation - Minikube Setup"
echo "======================================"
echo ""

# Function to check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Check if running on Linux
if [[ "$OSTYPE" != "linux-gnu"* ]]; then
    echo "This script is designed for Linux. Please adapt for your OS."
    exit 1
fi

# Install required dependencies
echo "[1/7] Installing dependencies..."
sudo apt-get update -qq
sudo apt-get install -y curl wget apt-transport-https ca-certificates gnupg lsb-release conntrack socat

# Install Docker if not present
if ! command_exists docker; then
    echo "[2/7] Installing Docker..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sudo sh get-docker.sh
    sudo usermod -aG docker $USER
    rm get-docker.sh
    echo "Docker installed. You may need to log out and back in for group changes to take effect."
else
    echo "[2/7] Docker already installed."
fi

# Install kubectl
if ! command_exists kubectl; then
    echo "[3/7] Installing kubectl..."
    curl -LO "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl"
    sudo install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl
    rm kubectl
    echo "kubectl installed: $(kubectl version --client --short 2>/dev/null || kubectl version --client)"
else
    echo "[3/7] kubectl already installed: $(kubectl version --client --short 2>/dev/null || kubectl version --client)"
fi

# Install Minikube
if ! command_exists minikube; then
    echo "[4/7] Installing Minikube..."
    curl -LO https://storage.googleapis.com/minikube/releases/latest/minikube-linux-amd64
    sudo install minikube-linux-amd64 /usr/local/bin/minikube
    rm minikube-linux-amd64
    echo "Minikube installed: $(minikube version --short)"
else
    echo "[4/7] Minikube already installed: $(minikube version --short)"
fi

# Install Helm (useful for Istio and other components)
if ! command_exists helm; then
    echo "[5/7] Installing Helm..."
    curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
    echo "Helm installed: $(helm version --short)"
else
    echo "[5/7] Helm already installed: $(helm version --short)"
fi

# Check if Minikube is running
if minikube status >/dev/null 2>&1; then
    echo "[6/7] Minikube is already running."
    read -p "Do you want to delete and recreate the cluster? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "Deleting existing Minikube cluster..."
        minikube delete
    else
        echo "Keeping existing cluster."
        exit 0
    fi
fi

# Start Minikube with appropriate resources
echo "[6/7] Starting Minikube cluster..."
echo "This may take several minutes..."
echo "Note: No CPU/disk limits - using all available host resources"
minikube start \
    --driver=docker \
    --kubernetes-version=stable \
    --extra-config=kubelet.max-pods=250

# Enable necessary addons
echo "[7/7] Enabling Minikube addons..."
minikube addons enable metrics-server
minikube addons enable ingress

# Verify installation
echo ""
echo "======================================"
echo "Verification"
echo "======================================"
echo "Minikube status:"
minikube status
echo ""
echo "Kubectl version:"
kubectl version --short 2>/dev/null || kubectl version
echo ""
echo "Cluster info:"
kubectl cluster-info
echo ""
echo "======================================"
echo "Setup Complete!"
echo "======================================"
echo ""
echo "Next steps:"
echo "1. Run: kubectl get nodes"
echo "2. Deploy Sock Shop: ./scripts/deploy-sock-shop.sh"
echo "3. See docs/01-minikube-setup.md for more information"
