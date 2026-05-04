#!/usr/bin/env bash
#
# Quick DDoS Mitigation Comparison
# Runs short experiments to compare mitigation effectiveness
#

set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# Configuration
NAMESPACE="sock-shop"
ATTACK_DURATION="${ATTACK_DURATION:-60}"  # 1 minute
ATTACK_WORKERS="${ATTACK_WORKERS:-50}"
ATTACK_RATE="${ATTACK_RATE:-20}"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RESULTS_DIR="$PROJECT_ROOT/results/experiments/quick-comparison-$(date +%Y%m%d-%H%M%S)"

mkdir -p "$RESULTS_DIR"

echo -e "${CYAN}=== Quick Mitigation Comparison ===${NC}"
echo "Results: $RESULTS_DIR"
echo ""

# Get target URL
MINIKUBE_IP=$(minikube ip)
NODE_PORT=$(kubectl get svc -n "$NAMESPACE" front-end -o jsonpath='{.spec.ports[0].nodePort}')
TARGET_URL="http://${MINIKUBE_IP}:${NODE_PORT}"

echo "Target: $TARGET_URL"
echo ""

PROMETHEUS_URL="${PROMETHEUS_URL:-http://localhost:9090}"

# Query Prometheus and return a single float value (sum over results)
prom_query_sum() {
    local query="$1"
    local default="${2:-0}"
    python3 -c "
import urllib.request, json, urllib.parse, sys
url = '${PROMETHEUS_URL}/api/v1/query?query=' + urllib.parse.quote(sys.argv[1])
try:
    with urllib.request.urlopen(url, timeout=5) as r:
        d = json.load(r)
        vals = [float(m['value'][1]) for m in d['data']['result']]
        print(round(sum(vals), 4) if vals else ${default})
except Exception as e:
    print(${default})
" "$query" 2>/dev/null
}

# Function to collect metrics
collect_metrics() {
    local label="$1"
    local file="$2"

    echo -e "${YELLOW}[$label]${NC} Collecting metrics..."

    local pods=$(kubectl get pods -n "$NAMESPACE" --field-selector=status.phase=Running --no-headers | wc -l)
    local hpa=$(kubectl get hpa -n "$NAMESPACE" --no-headers 2>/dev/null | wc -l)
    local netpol=$(kubectl get networkpolicies -n "$NAMESPACE" --no-headers 2>/dev/null | wc -l)

    # Multi-sample response time: 10 requests, compute avg + error rate
    local success=0 errors=0 total_time=0
    for i in $(seq 1 10); do
        local result
        result=$(curl -o /dev/null -s -w '%{time_total}:%{http_code}' --connect-timeout 3 "$TARGET_URL" 2>/dev/null || echo "0:0")
        local t code
        t=$(echo "$result" | cut -d: -f1)
        code=$(echo "$result" | cut -d: -f2)
        if [ "$code" = "200" ]; then
            success=$((success + 1))
            total_time=$(echo "$total_time $t" | awk '{printf "%.6f", $1 + $2}')
        else
            errors=$((errors + 1))
        fi
    done
    local avg_response_s error_rate_pct
    avg_response_s=$(echo "$total_time $success" | awk '{if($2>0) printf "%.6f", $1/$2; else print "0"}')
    error_rate_pct=$(echo "$errors" | awk '{printf "%.1f", $1/10*100}')

    # Prometheus: CPU (millicores) and memory (MB) for critical services
    local svc_pods='front-end.*|catalogue.*|carts.*|orders.*|user.*|payment.*'
    local cpu_mcores
    cpu_mcores=$(prom_query_sum \
        "sum(rate(container_cpu_usage_seconds_total{namespace=\"${NAMESPACE}\",pod=~\"${svc_pods}\"}[1m]))" 0)
    cpu_mcores=$(echo "$cpu_mcores" | awk '{printf "%.2f", $1 * 1000}')

    local mem_mb
    mem_mb=$(prom_query_sum \
        "sum(container_memory_working_set_bytes{namespace=\"${NAMESPACE}\",pod=~\"${svc_pods}\"})" 0)
    mem_mb=$(echo "$mem_mb" | awk '{printf "%.1f", $1 / 1024 / 1024}')

    # Prometheus: front-end pod CPU rate for focused view
    local frontend_cpu_mcores
    frontend_cpu_mcores=$(prom_query_sum \
        "sum(rate(container_cpu_usage_seconds_total{namespace=\"${NAMESPACE}\",pod=~\"front-end.*\"}[1m]))" 0)
    frontend_cpu_mcores=$(echo "$frontend_cpu_mcores" | awk '{printf "%.2f", $1 * 1000}')

    cat > "$file" <<EOF
{
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "label": "$label",
  "pods": $pods,
  "hpa_count": $hpa,
  "network_policies": $netpol,
  "response_time_avg_s": "$avg_response_s",
  "error_rate_pct": $error_rate_pct,
  "cpu_services_mcores": $cpu_mcores,
  "memory_services_mb": $mem_mb,
  "frontend_cpu_mcores": $frontend_cpu_mcores
}
EOF
}

# Function to run attack
run_attack() {
    local phase="$1"
    local logfile="$2"
    
    echo -e "${CYAN}Running attack: $phase${NC}"
    
    cd "$PROJECT_ROOT/attacks"
    
    timeout $((ATTACK_DURATION + 10)) python3 -c "
import requests
import time
from concurrent.futures import ThreadPoolExecutor

url = '$TARGET_URL'
duration = $ATTACK_DURATION
workers = $ATTACK_WORKERS
rate = $ATTACK_RATE

def attack_worker(worker_id):
    start = time.time()
    requests_sent = 0
    while time.time() - start < duration:
        try:
            requests.get(url, timeout=2)
            requests_sent += 1
            time.sleep(1.0 / rate)
        except:
            pass
    return requests_sent

with ThreadPoolExecutor(max_workers=workers) as executor:
    futures = [executor.submit(attack_worker, i) for i in range(workers)]
    results = [f.result() for f in futures]
    
print(f'Total requests: {sum(results)}')
" > "$logfile" 2>&1 &
    
    local pid=$!
    sleep 10
    echo "  [10s] Attack in progress (PID: $pid)..."
    sleep 10
    echo "  [20s] Collecting mid-attack metrics..."
    collect_metrics "$phase-during" "$RESULTS_DIR/metrics-during-$phase.json"
    wait $pid || true
    
    echo "  Attack complete, waiting 30s for stabilization..."
    sleep 30
}

# Deploy native mitigations
deploy_native() {
    echo -e "${CYAN}Deploying Native Kubernetes mitigations...${NC}"
    
    kubectl apply -f "$PROJECT_ROOT/mitigation/kubernetes-native/network-policies/" 2>&1 | head -5
    kubectl apply -f "$PROJECT_ROOT/mitigation/kubernetes-native/autoscaling/" 2>&1 | head -5
    kubectl apply -f "$PROJECT_ROOT/mitigation/kubernetes-native/resource-quotas/" 2>&1 | head -5
    
    echo "Waiting 30s for mitigations to activate..."
    sleep 30
}

# Deploy nephio mitigations
deploy_nephio() {
    echo -e "${CYAN}Deploying Nephio mitigations...${NC}"
    
    if [ -f "$PROJECT_ROOT/mitigation/nephio/deploy.sh" ]; then
        bash "$PROJECT_ROOT/mitigation/nephio/deploy.sh" 2>&1 | head -20
    else
        kubectl apply -f "$PROJECT_ROOT/mitigation/nephio/translated/" 2>&1 | head -10
    fi
    
    echo "Waiting 30s for Nephio mitigations to activate..."
    sleep 30
}

# Cleanup
cleanup_all() {
    echo -e "${CYAN}Cleaning up mitigations...${NC}"
    kubectl delete hpa,networkpolicies,resourcequotas --all -n "$NAMESPACE" --ignore-not-found=true 2>/dev/null || true
    sleep 10
}

#=============================================================================
# EXPERIMENT
#=============================================================================

echo -e "${GREEN}=== PHASE 1: BASELINE (No Mitigations) ===${NC}"
cleanup_all
collect_metrics "baseline-pre" "$RESULTS_DIR/metrics-pre-baseline.json"
run_attack "baseline" "$RESULTS_DIR/attack-baseline.log"
collect_metrics "baseline-post" "$RESULTS_DIR/metrics-post-baseline.json"

echo ""
echo -e "${GREEN}=== PHASE 2: NATIVE KUBERNETES MITIGATIONS ===${NC}"
deploy_native
collect_metrics "native-pre" "$RESULTS_DIR/metrics-pre-native.json"
run_attack "native" "$RESULTS_DIR/attack-native.log"
collect_metrics "native-post" "$RESULTS_DIR/metrics-post-native.json"

echo ""
echo -e "${GREEN}=== PHASE 3: NEPHIO MITIGATIONS ===${NC}"
deploy_nephio
collect_metrics "nephio-pre" "$RESULTS_DIR/metrics-pre-nephio.json"
run_attack "nephio" "$RESULTS_DIR/attack-nephio.log"
collect_metrics "nephio-post" "$RESULTS_DIR/metrics-post-nephio.json"

#=============================================================================
# GENERATE SUMMARY TABLE
#=============================================================================

echo ""
echo -e "${GREEN}=== RESULTS SUMMARY ===${NC}"
echo ""

cat > "$RESULTS_DIR/summary.md" <<EOF
# Mitigation Comparison Results

## Configuration
- Attack Duration: ${ATTACK_DURATION}s
- Workers: ${ATTACK_WORKERS}
- Rate: ${ATTACK_RATE} req/s/worker
- Total Rate: $((ATTACK_WORKERS * ATTACK_RATE)) req/s

## Results

| Scenario | Phase | Pods | HPAs | NetPols | Avg Response (s) | Error Rate % | CPU (mcores) | Mem (MB) | FE CPU (mc) |
|----------|-------|------|------|---------|------------------|--------------|--------------|----------|-------------|
EOF

# Parse metrics and add to table
for scenario in baseline native nephio; do
    for phase in pre during post; do
        file="$RESULTS_DIR/metrics-${phase}-${scenario}.json"
        if [ -f "$file" ]; then
            pods=$(jq -r '.pods' "$file" 2>/dev/null || echo "N/A")
            hpa=$(jq -r '.hpa_count' "$file" 2>/dev/null || echo "0")
            netpol=$(jq -r '.network_policies' "$file" 2>/dev/null || echo "0")
            response=$(jq -r '.response_time_avg_s' "$file" 2>/dev/null || echo "N/A")
            errrate=$(jq -r '.error_rate_pct' "$file" 2>/dev/null || echo "N/A")
            cpu=$(jq -r '.cpu_services_mcores' "$file" 2>/dev/null || echo "N/A")
            mem=$(jq -r '.memory_services_mb' "$file" 2>/dev/null || echo "N/A")
            fe_cpu=$(jq -r '.frontend_cpu_mcores' "$file" 2>/dev/null || echo "N/A")

            echo "| $scenario | $phase | $pods | $hpa | $netpol | $response | $errrate | $cpu | $mem | $fe_cpu |" >> "$RESULTS_DIR/summary.md"
        fi
    done
done

cat "$RESULTS_DIR/summary.md"

echo ""
echo -e "${GREEN}Experiment complete!${NC}"
echo "Results saved to: $RESULTS_DIR"
echo ""
echo "View summary:"
echo "  cat $RESULTS_DIR/summary.md"
echo ""
