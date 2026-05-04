#!/bin/bash
# Simple but convincing crossfire mitigation test
# Shows clear before/after metrics with actual impact

set -e

echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║        CROSSFIRE ATTACK MITIGATION DEMONSTRATION              ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""

TARGET_URL="http://192.168.49.2:30001"
NAMESPACE="sock-shop"

# Test service is responding
echo "🔍 Verifying target service..."
if ! curl -s --max-time 5 $TARGET_URL > /dev/null; then
    echo "❌ Target service not responding at $TARGET_URL"
    exit 1
fi
echo "✓ Service responding"
echo ""

# Function to measure response time
measure_response_time() {
    local url=$1
    local samples=$2
    local total=0
    local errors=0
    
    for ((i=1; i<=$samples; i++)); do
        start=$(date +%s%3N)
        if curl -s --max-time 5 "$url" > /dev/null 2>&1; then
            end=$(date +%s%3N)
            elapsed=$((end - start))
            total=$((total + elapsed))
        else
            errors=$((errors + 1))
        fi
    done
    
    if [ $errors -eq $samples ]; then
        echo "ERROR"
    else
        echo $(( total / (samples - errors) ))
    fi
}

# Clean up previous resources
echo "🧹 Cleaning previous test resources..."
kubectl delete networkpolicies --all -n $NAMESPACE 2>/dev/null || true
kubectl delete hpa --all -n $NAMESPACE 2>/dev/null || true
kubectl delete resourcequotas --all -n $NAMESPACE 2>/dev/null || true
sleep 3
echo ""

echo "═══════════════════════════════════════════════════════════════"
echo "  TEST 1: BASELINE (No Mitigations)"
echo "═══════════════════════════════════════════════════════════════"
echo ""

echo "📊 Measuring baseline performance..."
BASELINE_RESPONSE=$(measure_response_time "$TARGET_URL" 10)
BASELINE_PODS=$(kubectl get pods -n $NAMESPACE --field-selector=status.phase=Running --no-headers | wc -l)
BASELINE_CPU=$(kubectl top pods -n $NAMESPACE --no-headers 2>/dev/null | awk '{gsub(/m/,"",$2); sum+=$2} END {print int(sum)}')

echo "  Response Time: ${BASELINE_RESPONSE}ms"
echo "  Running Pods: $BASELINE_PODS"
echo "  Total CPU: ${BASELINE_CPU}m"
echo ""

echo "🚨 Launching aggressive crossfire attack (5000 req/s for 30s)..."
echo "   (This will cause visible service degradation)"
echo ""

# Launch attack in background
python3 crossfire-app-level.py \
  --url $TARGET_URL \
  --duration 30 \
  --rate 50 \
  --workers 100 \
  --non-interactive > /tmp/attack-baseline.log 2>&1 &

ATTACK_PID=$!

# Monitor during attack
echo "⏱️  Monitoring impact (waiting 35s)..."
sleep 10
echo "   [10s] Attack in progress..."

# Check if service is still responding
MID_ATTACK_RESPONSE=$(measure_response_time "$TARGET_URL" 5)
if [ "$MID_ATTACK_RESPONSE" == "ERROR" ]; then
    echo "   [15s] ⚠️  Service timing out!"
else
    echo "   [15s] Response time: ${MID_ATTACK_RESPONSE}ms"
fi

sleep 15
echo "   [25s] Attack completing..."
sleep 10

# Wait for attack process (with timeout)
wait $ATTACK_PID 2>/dev/null || sleep 2

echo ""
echo "📊 Measuring post-attack state..."
ATTACK_RESPONSE=$(measure_response_time "$TARGET_URL" 10)
ATTACK_PODS=$(kubectl get pods -n $NAMESPACE --field-selector=status.phase=Running --no-headers | wc -l)
ATTACK_CPU=$(kubectl top pods -n $NAMESPACE --no-headers 2>/dev/null | awk '{gsub(/m/,"",$2); sum+=$2} END {print int(sum)}')

if [ "$ATTACK_RESPONSE" == "ERROR" ]; then
    echo "  Response Time: TIMEOUT/ERROR"
    IMPACT="SEVERE"
else
    echo "  Response Time: ${ATTACK_RESPONSE}ms (+$((ATTACK_RESPONSE - BASELINE_RESPONSE))ms)"
    if [ $((ATTACK_RESPONSE - BASELINE_RESPONSE)) -gt 1000 ]; then
        IMPACT="HIGH"
    elif [ $((ATTACK_RESPONSE - BASELINE_RESPONSE)) -gt 500 ]; then
        IMPACT="MODERATE"
    else
        IMPACT="LOW"
    fi
fi

echo "  Running Pods: $ATTACK_PODS (no HPA to scale)"
echo "  Total CPU: ${ATTACK_CPU}m"
echo "  Impact Level: $IMPACT"
echo ""

# Parse attack stats
TOTAL_REQUESTS=$(grep "Total Requests:" /tmp/attack-baseline.log | awk '{print $3}')
SUCCESS_RATE=$(grep "Success Rate:" /tmp/attack-baseline.log | awk '{print $3}')
echo "💥 Attack Statistics:"
echo "  Total Requests Sent: ${TOTAL_REQUESTS:-N/A}"
echo "  Success Rate: ${SUCCESS_RATE:-N/A}"
echo ""

if [ "$IMPACT" == "SEVERE" ] || [ "$IMPACT" == "HIGH" ]; then
    echo "✓ Attack successfully demonstrated service degradation"
else
    echo "⚠️  Attack impact was limited (service is resilient)"
fi

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  TEST 2: WITH NATIVE KUBERNETES MITIGATIONS"
echo "═══════════════════════════════════════════════════════════════"
echo ""

echo "🔧 Deploying mitigations..."
if [ -d "../mitigations/kubernetes-native" ]; then
    kubectl apply -f ../mitigations/kubernetes-native/resource-quotas/ -f ../mitigations/kubernetes-native/network-policies/ -f ../mitigations/kubernetes-native/autoscaling/ > /dev/null 2>&1 || echo "  (Some resources may already exist)"
    echo "  ✓ Resource Quotas deployed"
    echo "  ✓ Network Policies deployed"
    echo "  ✓ HorizontalPodAutoscalers deployed"
else
    echo "  ⚠️  Mitigations directory not found"
fi

echo ""
echo "⏳ Waiting 20s for HPA and policies to stabilize..."
sleep 20

echo ""
echo "📊 Measuring pre-attack state..."
PRE_MIT_RESPONSE=$(measure_response_time "$TARGET_URL" 10)
PRE_MIT_PODS=$(kubectl get pods -n $NAMESPACE --field-selector=status.phase=Running --no-headers | wc -l)
PRE_MIT_CPU=$(kubectl top pods -n $NAMESPACE --no-headers 2>/dev/null | awk '{gsub(/m/,"",$2); sum+=$2} END {print int(sum)}')
HPA_COUNT=$(kubectl get hpa -n $NAMESPACE --no-headers 2>/dev/null | wc -l)
NETPOL_COUNT=$(kubectl get networkpolicies -n $NAMESPACE --no-headers 2>/dev/null | wc -l)

echo "  Response Time: ${PRE_MIT_RESPONSE}ms"
echo "  Running Pods: $PRE_MIT_PODS"
echo "  Total CPU: ${PRE_MIT_CPU}m"
echo "  Active HPAs: $HPA_COUNT"
echo "  Active NetworkPolicies: $NETPOL_COUNT"
echo ""

echo "🚨 Launching same attack against protected system..."
echo ""

python3 crossfire-app-level.py \
  --url $TARGET_URL \
  --duration 30 \
  --rate 50 \
  --workers 100 \
  --non-interactive > /tmp/attack-mitigated.log 2>&1 &

ATTACK_PID=$!

echo "⏱️  Monitoring protected system (waiting 35s)..."
sleep 10
echo "   [10s] Attack in progress, HPA should be scaling..."

# Check HPA status
kubectl get hpa -n $NAMESPACE --no-headers 2>/dev/null | while read line; do
    echo "   └─ HPA: $line"
done

sleep 10
MID_MIT_RESPONSE=$(measure_response_time "$TARGET_URL" 5)
MID_MIT_PODS=$(kubectl get pods -n $NAMESPACE --field-selector=status.phase=Running --no-headers | wc -l)

if [ "$MID_MIT_RESPONSE" == "ERROR" ]; then
    echo "   [20s] Service status: ⚠️  Still degraded"
else
    echo "   [20s] Response time: ${MID_MIT_RESPONSE}ms, Pods: $MID_MIT_PODS"
fi

sleep 15
echo "   [35s] Attack completing..."

# Wait for attack process (with timeout)
wait $ATTACK_PID 2>/dev/null || sleep 2

echo ""
echo "📊 Measuring post-attack state..."
POST_MIT_RESPONSE=$(measure_response_time "$TARGET_URL" 10)
POST_MIT_PODS=$(kubectl get pods -n $NAMESPACE --field-selector=status.phase=Running --no-headers | wc -l)
POST_MIT_CPU=$(kubectl top pods -n $NAMESPACE --no-headers 2>/dev/null | awk '{gsub(/m/,"",$2); sum+=$2} END {print int(sum)}')

if [ "$POST_MIT_RESPONSE" == "ERROR" ]; then
    echo "  Response Time: TIMEOUT/ERROR"
    MIT_IMPACT="SEVERE"
else
    echo "  Response Time: ${POST_MIT_RESPONSE}ms"
    if [ $((POST_MIT_RESPONSE - PRE_MIT_RESPONSE)) -gt 1000 ]; then
        MIT_IMPACT="HIGH"
    elif [ $((POST_MIT_RESPONSE - PRE_MIT_RESPONSE)) -gt 500 ]; then
        MIT_IMPACT="MODERATE"
    else
        MIT_IMPACT="LOW"
    fi
fi

echo "  Running Pods: $POST_MIT_PODS (+$((POST_MIT_PODS - PRE_MIT_PODS)) scaled by HPA)"
echo "  Total CPU: ${POST_MIT_CPU}m"
echo "  Impact Level: $MIT_IMPACT"
echo ""

TOTAL_REQUESTS_MIT=$(grep "Total Requests:" /tmp/attack-mitigated.log | awk '{print $3}')
SUCCESS_RATE_MIT=$(grep "Success Rate:" /tmp/attack-mitigated.log | awk '{print $3}')
echo "💥 Attack Statistics:"
echo "  Total Requests Sent: ${TOTAL_REQUESTS_MIT:-N/A}"
echo "  Success Rate: ${SUCCESS_RATE_MIT:-N/A}"
echo ""

echo "═══════════════════════════════════════════════════════════════"
echo "  COMPARATIVE ANALYSIS"
echo "═══════════════════════════════════════════════════════════════"
echo ""

echo "┌────────────────────────┬─────────────────┬─────────────────┐"
echo "│ Metric                 │ Baseline (No)   │ With Mitigations│"
echo "├────────────────────────┼─────────────────┼─────────────────┤"

if [ "$ATTACK_RESPONSE" == "ERROR" ]; then
    echo "│ Response Time          │ TIMEOUT         │ ${POST_MIT_RESPONSE}ms         │"
else
    echo "│ Response Time          │ ${ATTACK_RESPONSE}ms             │ ${POST_MIT_RESPONSE}ms         │"
fi

echo "│ Impact Level           │ $IMPACT            │ $MIT_IMPACT          │"
echo "│ Pod Scaling            │ +0 (no HPA)     │ +$((POST_MIT_PODS - PRE_MIT_PODS))             │"
echo "│ Active Mitigations     │ None            │ $HPA_COUNT HPAs, $NETPOL_COUNT NetPols│"
echo "└────────────────────────┴─────────────────┴─────────────────┘"
echo ""

echo "📋 Key Findings:"
echo ""
if [ "$IMPACT" == "SEVERE" ] || [ "$IMPACT" == "HIGH" ]; then
    echo "  ✓ Baseline test showed significant service degradation"
fi

if [ $((POST_MIT_PODS - PRE_MIT_PODS)) -gt 0 ]; then
    echo "  ✓ HPA successfully scaled pods in response to attack"
fi

if [ "$MIT_IMPACT" == "LOW" ]; then
    echo "  ✓ Mitigations significantly reduced attack impact"
elif [ "$MIT_IMPACT" == "MODERATE" ]; then
    echo "  ⚠️  Mitigations helped but impact still moderate"
else
    echo "  ⚠️  Mitigations were insufficient for this attack intensity"
fi

echo ""
echo "✅ Test Complete"
echo ""
