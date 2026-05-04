#!/bin/bash
# Collect real CIC-DDoS2019 features from network traffic

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================="
echo "CIC-DDoS2019 Feature Collection"
echo "=========================================="
echo
echo "This will capture network packets and extract CIC-DDoS2019 features:"
echo "  - Packet-level flow statistics"
echo "  - TCP/UDP flags and timing"
echo "  - Inter-arrival times"
echo "  - Window sizes and headers"
echo
echo "Phases:"
echo "  1. Normal traffic baseline (60s)"
echo "  2. HTTP Flood attack (45s)"
echo "  3. SYN Flood attack (45s)"
echo "  4. UDP Flood attack (45s)"
echo "  5. Slowloris attack (45s)"
echo
echo "Total time: ~4 minutes"
echo
echo "NOTE: Requires sudo for packet capture (tcpdump)"
echo

if ! command -v tcpdump >/dev/null 2>&1; then
    echo "ERROR: tcpdump not found. Install tcpdump before continuing."
    exit 1
fi

if ! sudo -n true >/dev/null 2>&1; then
    echo "Requesting sudo credentials for tcpdump captures..."
    sudo -v
fi

read -p "Press Enter to start or Ctrl+C to cancel..."

# Get frontend URL without invoking interactive minikube service helper
NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null || true)
NODEPORT=$(kubectl get svc front-end -n sock-shop -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || true)

if [ -z "$NODE_IP" ] || [ -z "$NODEPORT" ]; then
    echo "ERROR: Could not determine Sock Shop front-end endpoint."
    echo "Check that Minikube is running and front-end service exists."
    exit 1
fi

FRONTEND_URL="http://${NODE_IP}:${NODEPORT}"
echo "Frontend URL: $FRONTEND_URL"
echo

# Get network interface for minikube traffic
echo "Detecting network interface..."
INTERFACE=$(ip route get $(echo $FRONTEND_URL | sed 's|http://||' | cut -d: -f1) | grep -oP 'dev \K\S+' || echo "any")
echo "Using interface: $INTERFACE"
echo

# Activate repo virtualenv
VENV_PATH="${REPO_ROOT}/venv/bin/activate"
if [ ! -f "$VENV_PATH" ]; then
    echo "ERROR: Virtualenv not found at $VENV_PATH"
    echo "Create it with: python3 -m venv venv && source venv/bin/activate && pip install -r attack-simulations/requirements.txt && pip install -r ml-detection/requirements.txt"
    exit 1
fi

source "$VENV_PATH"

# Install scapy if needed
pip install -q scapy 2>/dev/null || true

# Clean up old data
rm -f ml-detection/cicddos_real_features.csv

# 1. Normal traffic
echo "=========================================="
echo "Phase 1: Normal Traffic Baseline"
echo "=========================================="
echo

# Start baseline traffic
python3 "$REPO_ROOT/traffic-generator.py" --target-url "$FRONTEND_URL" --workers 5 --rate 10 > /dev/null 2>&1 &
TRAFFIC_PID=$!

sleep 5  # Let traffic stabilize

# Capture and extract features
python3 "$REPO_ROOT/ml-detection/collect_cicddos_features.py" \
    --interface "$INTERFACE" \
    --duration 60 \
    --label normal \
    --output "$REPO_ROOT/ml-detection/cicddos_real_features.csv"

kill $TRAFFIC_PID 2>/dev/null || true
sleep 5

# 2-5. Attack scenarios
ATTACKS=("http-flood" "syn-flood" "udp-flood" "slowloris")

for ATTACK in "${ATTACKS[@]}"; do
    echo
    echo "=========================================="
    echo "Phase: $ATTACK Attack"
    echo "=========================================="
    echo
    
    # Keep some baseline traffic
    python3 "$REPO_ROOT/traffic-generator.py" --target-url "$FRONTEND_URL" --workers 3 --rate 5 > /dev/null 2>&1 &
    TRAFFIC_PID=$!
    
    sleep 2
    
    # Start attack
    python3 "$REPO_ROOT/attack-simulations/attack.py" \
        --target-url "$FRONTEND_URL" \
        --attack-type "$ATTACK" \
        --workers 100 \
        --rate 30 \
        --duration 50 > /dev/null 2>&1 &
    ATTACK_PID=$!
    
    sleep 3  # Let attack ramp up
    
    # Capture and extract features
    python3 "$REPO_ROOT/ml-detection/collect_cicddos_features.py" \
        --interface "$INTERFACE" \
        --duration 45 \
        --label "$ATTACK" \
        --output "$REPO_ROOT/ml-detection/cicddos_real_features.csv"
    
    # Clean up
    kill $TRAFFIC_PID 2>/dev/null || true
    kill $ATTACK_PID 2>/dev/null || true
    sleep 5
done

echo
echo "=========================================="
echo "✓ Feature Collection Complete!"
echo "=========================================="
echo
echo "Training data saved to: ml-detection/cicddos_real_features.csv"
echo

# Show summary
python3 -c "
import pandas as pd
df = pd.read_csv('${REPO_ROOT}/ml-detection/cicddos_real_features.csv')
print('Dataset Summary:')
print(f'  Total flows: {len(df)}')
print(f'  Features: {len(df.columns) - 2}')
print()
print('Label distribution:')
print(df['label'].value_counts())
print()
print('Key feature statistics by label:')
stats_cols = ['flow_packets_per_sec', 'syn_flag_count', 'psh_flag_count', 'flow_bytes_per_sec']
for col in stats_cols:
    if col in df.columns:
        print(f'\n{col}:')
        print(df.groupby('label')[col].agg(['mean', 'std']))
"

echo
echo "Next step: Train CIC-DDoS2019 model on real features"
echo "  ./train-cicddos-model.sh"
