#!/bin/bash

# Test script for verifying attack detection and classification
# Tests multiple attack types and validates proper classification

MINIKUBE_IP=$(minikube ip)
TARGET_URL="http://${MINIKUBE_IP}:31987"

echo "=========================================="
echo "DDoS Attack Detection Test Suite"
echo "Target: $TARGET_URL"
echo "=========================================="
echo

test_attack() {
    local attack_type=$1
    local workers=$2
    local rate=$3
    local expected_classification=$4
    
    echo "-------------------------------------------"
    echo "Testing: $attack_type"
    echo "Expected Classification: $expected_classification"
    echo "-------------------------------------------"
    
    # Clean up any running processes
    pkill -f "detector.py\|traffic-generator.py\|kubectl port-forward.*prometheus" 2>/dev/null
    sleep 2
    
    # Clear log
    rm -f ml-detection/detector.log
    
    # Start detector
    echo "Starting detector..."
    ./start.sh > /dev/null 2>&1 &
    sleep 5
    
    # Start normal traffic
    echo "Starting baseline traffic..."
    venv/bin/python3 traffic-generator.py --target-url "$TARGET_URL" --workers 5 --rate 10 > /dev/null 2>&1 &
    
    # Wait for baseline collection
    echo "Collecting baseline (100 seconds)..."
    sleep 100
    
    # Launch attack
    echo "Launching $attack_type attack..."
    venv/bin/python3 attack-simulations/attack.py \
        --target-url "$TARGET_URL" \
        --attack-type "$attack_type" \
        --workers "$workers" \
        --duration 30 \
        --rate "$rate"
    
    # Wait for detection
    echo "Waiting for detection..."
    sleep 35
    
    # Check results
    echo
    echo "Detection Results:"
    echo "=================="
    tail -30 ml-detection/detector.log | grep -A 18 "DDOS ATTACK" | head -20
    echo
    echo "Spike Ratio:"
    tail -100 ml-detection/detector.log | grep "→" | tail -1
    echo
    
    # Clean up
    pkill -f "detector.py\|traffic-generator.py\|kubectl port-forward.*prometheus" 2>/dev/null
    
    echo
    read -p "Press Enter to continue to next test..."
    echo
}

# Test each attack type
test_attack "http-flood" 100 20 "HTTP Flood Attack"
test_attack "syn" 100 20 "SYN Flood Attack"  
test_attack "udp" 100 20 "UDP Flood Attack"
test_attack "slowloris" 50 5 "Slowloris/Slow HTTP Attack"

echo "=========================================="
echo "All tests completed!"
echo "=========================================="
