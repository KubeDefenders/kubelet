#!/bin/bash

# Sock Shop Deployment Script
# Deploys the Sock Shop microservices demo to Kubernetes

set -e

echo "======================================"
echo "Deploying Sock Shop to Kubernetes"
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
    echo "Start Minikube first: minikube start"
    exit 1
fi

# Create namespace
echo "[1/4] Creating sock-shop namespace..."
kubectl create namespace sock-shop --dry-run=client -o yaml | kubectl apply -f -

# Label namespace for Istio injection (if Istio is installed)
echo "[2/4] Labeling namespace for Istio sidecar injection..."
kubectl label namespace sock-shop istio-injection=enabled --overwrite

# Deploy Sock Shop using the complete deployment manifest
echo "[3/4] Deploying Sock Shop application..."
kubectl apply -f target/app/deploy/kubernetes/complete-demo.yaml

# Wait for deployments to be ready
echo "[4/4] Waiting for deployments to be ready (this may take a few minutes)..."
kubectl wait --for=condition=available --timeout=300s deployment --all -n sock-shop || true

echo ""
echo "======================================"
echo "Deployment Status"
echo "======================================"
kubectl get pods -n sock-shop
echo ""
kubectl get services -n sock-shop
echo ""
echo "======================================"
echo "Access Sock Shop"
echo "======================================"
echo ""
echo "To access the Sock Shop frontend:"
echo "1. Run: kubectl port-forward -n sock-shop svc/front-end 8080:80"
echo "2. Open: http://localhost:8080"
echo ""
echo "Or use Minikube service:"
echo "  minikube service front-end -n sock-shop"
echo ""
echo "To monitor pods:"
echo "  watch kubectl get pods -n sock-shop"
