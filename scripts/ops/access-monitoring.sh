#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../common.sh"

echo "======================================"
echo "Access Monitoring Dashboards"
echo "======================================"
echo ""

if ! command -v kubectl >/dev/null 2>&1; then
    echo "Error: kubectl is not installed"
    exit 1
fi

if ! kubectl cluster-info >/dev/null 2>&1; then
    echo "Error: Kubernetes cluster is not running"
    exit 1
fi

cleanup() {
    echo ""
    echo "Stopping port forwards..."
    pkill -P $$ kubectl >/dev/null 2>&1 || true
    exit 0
}

trap cleanup SIGINT SIGTERM

start_port_forward() {
    local namespace=$1
    local service=$2
    local local_port=$3
    local target_port=$4
    local name=$5
    local log_file="/tmp/${service}-${local_port}.log"

    echo "Starting ${name} on http://localhost:${local_port}"
    nohup kubectl port-forward -n "$namespace" svc/"$service" \
        "$local_port:$target_port" >"$log_file" 2>&1 &
    local pid=$!
    sleep 2

    if ps -p "$pid" >/dev/null 2>&1; then
        echo "✓ ${name} ready (log: $log_file, stop: kill $pid)"
    else
        echo "❌ Failed to start ${name}. See $log_file"
    fi
}

if kubectl get ns monitoring >/dev/null 2>&1; then
    start_port_forward monitoring grafana 3000 3000 "Grafana"
else
    start_port_forward istio-system grafana 3000 3000 "Grafana"
fi

start_port_forward istio-system prometheus 9090 9090 "Prometheus"
start_port_forward istio-system kiali 20001 20001 "Kiali"

echo ""
echo "Grafana:    http://localhost:3000"
echo "Prometheus: http://localhost:9090"
echo "Kiali:      http://localhost:20001"
echo ""
echo "Stop tunnels: kill the printed PIDs or run 'pkill -f "kubectl port-forward"'"
echo ""

wait
