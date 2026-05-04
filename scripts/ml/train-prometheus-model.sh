#!/bin/bash
# Train ML model on collected Prometheus metrics

set -e

cd "$(dirname "$0")/ml-detection"

echo "=========================================="
echo "Prometheus ML Model Training"
echo "=========================================="
echo

# Check if training data exists
if [ ! -f "prometheus_training_data.csv" ]; then
    echo "ERROR: Training data not found!"
    echo "Please collect training data first:"
    echo "  ./collect-real-training-data.sh"
    exit 1
fi

# Activate venv
source ../venv/bin/activate

# Install packages if needed
pip install -q scikit-learn shap lime pandas 2>/dev/null || true

# Train model
echo "Training model on real Prometheus metrics..."
echo
python3 train_prometheus_model.py \
    --training-data prometheus_training_data.csv \
    --model-path prometheus_trained_model.pkl

echo
echo "=========================================="
echo "✓ Training complete!"
echo "=========================================="
echo
echo "Model saved to: ml-detection/prometheus_trained_model.pkl"
echo
echo "To run the detector:"
echo "  ./start-prometheus-detector.sh"
echo
