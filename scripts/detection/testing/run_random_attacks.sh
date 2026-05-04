#!/bin/bash

cd /home/spuggle/dev/ddos/attack-simulations

TARGET_URL="http://192.168.49.2:30001"

ATTACK_TYPES=("http-flood" "slowloris" "syn" "udp" "dns" "ntp" "ldap" "mssql")

# Use provided parameters or randomize
if [ $# -eq 0 ]; then
    # No args - randomize everything
    ATTACK_TYPE=${ATTACK_TYPES[$RANDOM % ${#ATTACK_TYPES[@]}]}
    RATE=$(( RANDOM % 41 + 60 ))      # 60-100
    DURATION=$(( RANDOM % 61 + 30 ))  # 30-90
    WORKERS=$(( RANDOM % 5 + 4 ))     # 4-8
    echo "🎲 Random attack parameters:"
elif [ $# -eq 4 ]; then
    # All args provided
    ATTACK_TYPE=$1
    RATE=$2
    DURATION=$3
    WORKERS=$4
    echo "⚙️  Custom attack parameters:"
else
    echo "Usage: $0 [attack_type] [rate] [duration] [workers]"
    echo "  Or run with no arguments for random parameters"
    echo ""
    echo "Available attack types:"
    echo "  • http-flood  - Standard HTTP GET flood"
    echo "  • slowloris   - Slow HTTP connections (keeps connections open)"
    echo "  • syn         - TCP SYN flood"
    echo "  • udp         - UDP flood"
    echo "  • dns         - DNS amplification"
    echo "  • ntp         - NTP amplification"
    echo "  • ldap        - LDAP amplification"
    echo "  • mssql       - MSSQL amplification"
    echo ""
    echo "Examples:"
    echo "  $0                              # Random attack"
    echo "  $0 http-flood 85 60 6           # HTTP flood: 85 req/s, 60s, 6 workers"
    echo "  $0 slowloris 20 90 8            # Slowloris: 20 req/s, 90s, 8 workers"
    echo "  $0 syn 100 45 10                # SYN flood: 100 req/s, 45s, 10 workers"
    exit 1
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🚨 LAUNCHING ATTACK - $(date '+%Y-%m-%d %H:%M:%S')"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Type: $ATTACK_TYPE"
echo "  Target: $TARGET_URL"
echo "  Rate: ${RATE} req/s/worker"
echo "  Workers: $WORKERS"
echo "  Duration: ${DURATION}s"
echo "  Total rate: $((RATE * WORKERS)) req/s"
echo ""

# Launch attack
python3 attack.py \
    --target-url "$TARGET_URL" \
    --attack-type "$ATTACK_TYPE" \
    --duration "$DURATION" \
    --rate "$RATE" \
    --workers "$WORKERS"

echo ""
echo "✓ Attack completed"
