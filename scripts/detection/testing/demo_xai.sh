#!/bin/bash
# Demo script showing xAI explanations during normal and attack traffic

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║     ML DDoS Detector with Explainable AI (SHAP) Demo          ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""

cd /home/spuggle/dev/ddos/ml-detector

# Test 1: Normal traffic baseline
echo "📊 Test 1: Checking Normal Traffic (Baseline)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
python3 practical_detector.py detect --explain 2>&1 | grep -v "INFO"
echo ""
sleep 3

# Test 2: Launch attack and show detection with explanation
echo "🚨 Test 2: Launching HTTP Flood Attack (80 req/s, 6 workers)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
cd /home/spuggle/dev/ddos/attack-simulations
python3 attack.py --target-url http://192.168.49.2:30001 \
    --attack-type http-flood \
    --duration 30 \
    --rate 80 \
    --workers 6 > /tmp/demo_attack.log 2>&1 &
ATTACK_PID=$!
echo "Attack launched (PID: $ATTACK_PID)"
cd /home/spuggle/dev/ddos/ml-detector
sleep 8

echo ""
echo "🔍 Detection Check #1 (During Attack):"
echo "─────────────────────────────────────"
python3 practical_detector.py detect --explain 2>&1 | grep -v "INFO"
echo ""
sleep 5

echo "🔍 Detection Check #2 (During Attack):"
echo "─────────────────────────────────────"
python3 practical_detector.py detect --explain 2>&1 | grep -v "INFO"
echo ""
sleep 5

echo "🔍 Detection Check #3 (During Attack):"
echo "─────────────────────────────────────"
python3 practical_detector.py detect --explain 2>&1 | grep -v "INFO"
echo ""

# Wait for attack to complete
echo "⏳ Waiting for attack to complete..."
wait $ATTACK_PID 2>/dev/null
sleep 10

# Test 3: Post-attack normal
echo ""
echo "✅ Test 3: Checking Post-Attack (Should Return to Normal)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
python3 practical_detector.py detect --explain 2>&1 | grep -v "INFO"
echo ""

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║                       Demo Complete                            ║"
echo "╚════════════════════════════════════════════════════════════════╝"
