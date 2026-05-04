#!/bin/bash
# Run realistic traffic generation continuously
# Press Ctrl+C to stop

cd /home/spuggle/dev/ddos/ml-detector

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║        Realistic Traffic Generator (Locust-based)             ║"
echo "║                   Press Ctrl+C to stop                         ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""

# Default values
USERS=${1:-12}
INTENSIVE_RATIO=${2:-0.2}
URL="http://192.168.49.2:30001"

echo "Configuration:"
echo "  • Target: $URL"
echo "  • Users: $USERS (${INTENSIVE_RATIO}% power users)"
echo "  • Duration: Continuous (until Ctrl+C)"
echo ""
echo "Starting traffic generation..."
echo ""

python3 examples/realistic_traffic_generator.py \
    --url "$URL" \
    --users "$USERS" \
    --intensive-ratio "$INTENSIVE_RATIO"
