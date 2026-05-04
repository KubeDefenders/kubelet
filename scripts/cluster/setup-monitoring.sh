#!/bin/bash

# Monitoring Stack Setup Script
# Configures Kiali, Grafana, and Prometheus for observability

set -euo pipefail

PROM_OPERATOR_VERSION="v0.69.0"
SERVICE_MONITOR_CRD_URL="https://raw.githubusercontent.com/prometheus-operator/prometheus-operator/${PROM_OPERATOR_VERSION}/example/prometheus-operator-crd/monitoring.coreos.com_servicemonitors.yaml"

echo "======================================"
echo "Setting Up Monitoring Stack"
echo "======================================"
echo ""

ensure_servicemonitor_crd() {
    if kubectl get crd servicemonitors.monitoring.coreos.com >/dev/null 2>&1; then
        echo "ServiceMonitor CRD already installed"
        return
    fi

    echo "Installing ServiceMonitor CRD (${PROM_OPERATOR_VERSION})..."
    kubectl apply --validate=false -f "${SERVICE_MONITOR_CRD_URL}"
}

# Check if Istio is installed
if ! kubectl get namespace istio-system &> /dev/null; then
    echo "Error: Istio is not installed"
    echo "Install Istio first: ./scripts/setup-istio.sh"
    exit 1
fi

# The demo profile already includes Kiali, Grafana, and Prometheus
# We just need to verify they're running and create access methods

echo "[1/5] Verifying monitoring components..."

# Check if monitoring components exist
if ! kubectl get deployment kiali -n istio-system &> /dev/null; then
    echo "Error: Kiali not found. Make sure Istio was installed with demo profile."
    exit 1
fi

echo "Monitoring components found:"
kubectl get deployments -n istio-system | grep -E "(kiali|grafana|prometheus)"

echo ""
echo "[2/5] Applying custom Grafana dashboards..."
kubectl apply -f monitoring/grafana-dashboards.yaml

echo ""
echo "[3/5] Applying Prometheus ServiceMonitors..."
ensure_servicemonitor_crd
kubectl apply -f monitoring/servicemonitors.yaml

echo ""
echo "[4/5] Creating monitoring virtual services..."
# Already applied via istio/virtual-services.yaml
kubectl apply -f istio/virtual-services.yaml

echo ""
echo "[5/5] Verifying all pods are running..."
kubectl wait --for=condition=available --timeout=300s deployment/kiali -n istio-system
kubectl wait --for=condition=available --timeout=300s deployment/grafana -n istio-system
kubectl wait --for=condition=available --timeout=300s deployment/prometheus -n istio-system

echo ""
echo "======================================"
echo "Monitoring Stack Ready"
echo "======================================"
echo ""
echo "Access the monitoring tools:"
echo ""
echo "1. Kiali (Service Mesh Visualization):"
echo "   kubectl port-forward -n istio-system svc/kiali 20001:20001"
echo "   URL: http://localhost:20001"
echo ""
echo "2. Grafana (Metrics Dashboard):"
echo "   kubectl port-forward -n istio-system svc/grafana 3000:3000"
echo "   URL: http://localhost:3000"
echo ""
echo "3. Prometheus (Metrics Database):"
echo "   kubectl port-forward -n istio-system svc/prometheus 9090:9090"
echo "   URL: http://localhost:9090"
echo ""
echo "Or run the helper script:"
echo "  ./scripts/access-monitoring.sh"
echo ""
echo "See docs/04-monitoring-setup.md for detailed usage"
