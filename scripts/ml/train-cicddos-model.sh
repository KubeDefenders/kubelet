#!/bin/bash
# Train ML model using CIC-DDoS2019 representative dataset

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ML_DIR="${REPO_ROOT}/ml-detection"

cd "$ML_DIR"

echo "=========================================="
echo "CIC-DDoS2019 ML Model Training"
echo "=========================================="
echo

VENV_PATH="${REPO_ROOT}/venv/bin/activate"
if [ ! -f "$VENV_PATH" ]; then
	echo "ERROR: Repo virtualenv not found at $VENV_PATH"
	echo "Create it with: python3 -m venv venv && source venv/bin/activate && pip install -r attack-simulations/requirements.txt && pip install -r ml-detection/requirements.txt"
	exit 1
fi

source "$VENV_PATH"

# Install additional packages if needed
echo "Installing required packages..."
pip install -q scikit-learn shap lime 2>/dev/null || true

# Prepare dataset
echo
echo "Step 1: Preparing CIC-DDoS2019 dataset..."
python3 prepare_cicddos_dataset.py

# Train model
echo
echo "Step 2: Training model on CIC-DDoS2019 data..."
python3 cicddos_ml_detector.py --train --training-data cicddos2019_training_data.npz --model-path cicddos_trained_model.pkl

echo
echo "=========================================="
echo "✓ Training complete!"
echo "=========================================="
echo
echo "Model saved to: ml-detection/cicddos_trained_model.pkl"
echo
echo "To run the detector:"
echo "  ./start-cicddos-detector.sh"
echo
