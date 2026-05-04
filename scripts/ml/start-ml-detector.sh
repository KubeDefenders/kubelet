#!/bin/bash

# Script to start the ML-based DDoS detector

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../common.sh"
cd "$REPO_ROOT"

VENV_DIR="${REPO_ROOT}/venv"
MODEL_PATH="${REPO_ROOT}/ml-detection/trained_model.pkl"
CONFIG_PATH="${REPO_ROOT}/ml-detection/config.yaml"

echo "============================================"
echo "Starting ML DDoS Detector with SHAP/LIME"
echo "============================================"
echo

# Check model exists
if [ ! -f "$MODEL_PATH" ]; then
    echo "❌ Trained model not found at $MODEL_PATH"
    echo
    echo "Please train the model first:"
    echo "  ./train-ml-model.sh"
    echo
    exit 1
fi

# Check if Prometheus port-forward is already running
if pgrep -f "kubectl port-forward.*prometheus.*9090" > /dev/null; then
    echo "✓ Prometheus port-forward already running"
else
    echo "Starting Prometheus port-forward..."
    kubectl port-forward -n istio-system svc/prometheus 9090:9090 > /dev/null 2>&1 &
    sleep 3
    
    # Verify Prometheus is accessible
    if curl -s http://localhost:9090/-/healthy > /dev/null 2>&1; then
        echo "✓ Prometheus accessible at http://localhost:9090"
    else
        echo "❌ Prometheus not accessible"
        exit 1
    fi
fi

echo
echo "Starting ML detector..."
echo "Log: ml-detection/ml_detector.log"
echo

cd ml-detection

# Run detector
${VENV_DIR}/bin/python3 ml_detector.py \
    --config config.yaml \
    --model-path trained_model.pkl \
    --prometheus-url http://localhost:9090 \
    --interval 30 &

DETECTOR_PID=$!

echo
echo "============================================"
echo "✓ ML Detector running (PID: $DETECTOR_PID)"
echo "============================================"
echo
echo "The detector uses:"
echo "  • Random Forest classifier"
echo "  • SHAP for global feature importance"
echo "  • LIME for local instance explanations"
echo
echo "Monitor detections:"
echo "  tail -f ml-detection/ml_detector.log"
echo
echo "Stop detector:"
echo "  kill $DETECTOR_PID"
echo
