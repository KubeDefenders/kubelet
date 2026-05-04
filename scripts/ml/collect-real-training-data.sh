#!/bin/bash
# Collect real training data by running normal traffic and attacks

set -e

cd "$(dirname "$0")"

echo "=========================================="
echo "Training Data Collection Script"
echo "=========================================="
echo
echo "This will:"
echo "  1. Collect normal traffic baseline (60s)"
echo "  2. Run each attack type and collect data (45s each)"
echo "  3. Build training dataset from real metrics"
echo
echo "Total time: ~6 minutes"
echo
read -p "Press Enter to start or Ctrl+C to cancel..."

# Get frontend URL
FRONTEND_URL=$(minikube service front-end -n sock-shop --url 2>/dev/null | head -1)

if [ -z "$FRONTEND_URL" ]; then
    echo "ERROR: Could not get frontend URL. Is minikube running?"
    exit 1
fi

echo "Frontend URL: $FRONTEND_URL"
echo

# Start Prometheus port-forward if not running
if ! pgrep -f "kubectl port-forward.*prometheus" > /dev/null; then
    echo "Starting Prometheus port-forward..."
    kubectl port-forward -n istio-system svc/prometheus 9090:9090 > /dev/null 2>&1 &
    sleep 3
fi

# Activate venv
source ../venv/bin/activate

# Clean up any old data
rm -f ml-detection/prometheus_training_data.csv

# 1. Normal traffic baseline
echo "=========================================="
echo "Phase 1: Normal Traffic (60 seconds)"
echo "=========================================="
echo

# Start normal traffic
python3 ../traffic-generator.py --target-url "$FRONTEND_URL" --workers 5 --rate 10 > /dev/null 2>&1 &
TRAFFIC_PID=$!

sleep 5  # Let traffic stabilize

# Collect normal samples
python3 collect_training_data.py \
    --prometheus-url http://localhost:9090 \
    --duration 60 \
    --label normal \
    --output prometheus_training_data.csv

kill $TRAFFIC_PID 2>/dev/null || true
sleep 5

# 2. Attack scenarios
ATTACKS=("http-flood" "syn-flood" "udp-flood" "slowloris")

for ATTACK in "${ATTACKS[@]}"; do
    echo
    echo "=========================================="
    echo "Phase: $ATTACK Attack (45 seconds)"
    echo "=========================================="
    echo
    
    # Keep baseline traffic running
    python3 ../traffic-generator.py --target-url "$FRONTEND_URL" --workers 3 --rate 5 > /dev/null 2>&1 &
    TRAFFIC_PID=$!
    
    sleep 2
    
    # Start attack
    python3 ../attack-simulations/attack.py \
        --target-url "$FRONTEND_URL" \
        --attack-type "$ATTACK" \
        --workers 100 \
        --rate 30 \
        --duration 50 > /dev/null 2>&1 &
    ATTACK_PID=$!
    
    sleep 3  # Let attack ramp up
    
    # Collect attack samples
    python3 collect_training_data.py \
        --prometheus-url http://localhost:9090 \
        --duration 45 \
        --label "$ATTACK" \
        --output prometheus_training_data.csv
    
    # Clean up
    kill $TRAFFIC_PID 2>/dev/null || true
    kill $ATTACK_PID 2>/dev/null || true
    sleep 5
done

echo
echo "=========================================="
echo "✓ Data Collection Complete!"
echo "=========================================="
echo
echo "Training data saved to: ml-detection/prometheus_training_data.csv"
echo

# Show summary
python3 -c "
import pandas as pd
df = pd.read_csv('prometheus_training_data.csv')
print('Dataset Summary:')
print(f'  Total samples: {len(df)}')
print(f'  Features: {len(df.columns) - 2}')  # Exclude label and timestamp
print()
print('Label distribution:')
print(df['label'].value_counts())
print()
print('Sample statistics:')
print(df.groupby('label')[['request_rate', 'latency_p95', 'error_rate']].mean())
"

echo
echo "Next step: Train the model"
echo "  ./train-prometheus-model.sh"
