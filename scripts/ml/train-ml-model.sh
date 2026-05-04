#!/bin/bash

# Script to train the ML-based DDoS detector with SHAP/LIME

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../common.sh"
PROJECT_DIR="$REPO_ROOT"
VENV_DIR="${PROJECT_DIR}/venv"

cd "$PROJECT_DIR"

echo "============================================"
echo "ML DDoS Detector - Training Script"
echo "============================================"
echo

# Check if venv exists
if [ ! -d "$VENV_DIR" ]; then
    echo "❌ Virtual environment not found at $VENV_DIR"
    echo "Please create it first: python3 -m venv venv"
    exit 1
fi

# Activate venv
source "${VENV_DIR}/bin/activate"

echo "📦 Installing ML dependencies..."
pip install -q scikit-learn shap lime joblib

echo "✅ Dependencies installed"
echo

# Generate training data
echo "📊 Generating synthetic training data..."
cd ml-detection
python3 generate_training_data.py

if [ ! -f "training_data.csv" ]; then
    echo "❌ Training data generation failed"
    exit 1
fi

echo "✅ Training data generated"
echo

# Train model
echo "🤖 Training ML model with Random Forest..."
python3 ml_detector.py \
    --config config.yaml \
    --train \
    --training-data training_data.csv

if [ ! -f "trained_model.pkl" ]; then
    echo "❌ Model training failed"
    exit 1
fi

echo
echo "============================================"
echo "✅ Training Complete!"
echo "============================================"
echo
echo "Model saved to: ml-detection/trained_model.pkl"
echo "Training data: ml-detection/training_data.csv"
echo
echo "To run the ML detector:"
echo "  ./scripts/ml/start-ml-detector.sh"
echo
