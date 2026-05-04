#!/bin/bash
# Start CIC-DDoS2019 ML-based detector with SHAP/LIME

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../common.sh"
cd "$REPO_ROOT"

echo "Starting CIC-DDoS2019 ML Detector..."
echo

# Check if model exists
if [ ! -f "ml-detection/cicddos_trained_model.pkl" ]; then
    echo "ERROR: Model not found!"
    echo "Please train the model first:"
    echo "  ./train-cicddos-model.sh"
    exit 1
fi

# Check if Prometheus port-forward is running
if ! pgrep -f "kubectl port-forward.*prometheus" > /dev/null; then
    echo "Starting Prometheus port-forward..."
    kubectl port-forward -n istio-system svc/prometheus 9090:9090 > /dev/null 2>&1 &
    sleep 3
    echo "✓ Prometheus accessible at localhost:9090"
else
    echo "✓ Prometheus port-forward already running"
fi

echo

# Activate venv
source venv/bin/activate

# Run detector
cd ml-detection
echo "Starting detector (interval: 30s)..."
echo "Press Ctrl+C to stop"
echo
python3 cicddos_ml_detector.py \
    --config config.yaml \
    --model-path cicddos_trained_model.pkl \
    --prometheus-url http://localhost:9090 \
    --interval 30 \
    --prob-threshold 0.5 \
    --rate-spike-multiplier 3.0 \
    --min-rate 80
