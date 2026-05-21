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
ATTACK_DURATION="${ATTACK_DURATION:-180}"  # 3 minutes — long enough for HPAs to scale during attack
ATTACK_WORKERS="${ATTACK_WORKERS:-80}"
ATTACK_RATE="${ATTACK_RATE:-20}"
BACKGROUND_WORKERS="${BACKGROUND_WORKERS:-15}"
BACKGROUND_RATE="${BACKGROUND_RATE:-3}"
BACKGROUND_TRAFFIC_PID=""
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

# Background traffic — simulate real users alongside the attack
start_background_traffic() {
    echo "  [BG] Starting background normal traffic (${BACKGROUND_WORKERS} workers @ ${BACKGROUND_RATE} req/s each)..."
    _BG_URL="$TARGET_URL" _BG_W="$BACKGROUND_WORKERS" _BG_R="$BACKGROUND_RATE" \
    python3 -c "
import requests, time, threading, signal, os
url = os.environ['_BG_URL']
w = int(os.environ.get('_BG_W', '15'))
rate = float(os.environ.get('_BG_R', '3'))
stop = [False]
def worker():
    s = requests.Session()
    while not stop[0]:
        try: s.get(url, timeout=3)
        except: pass
        time.sleep(1.0 / rate)
threads = [threading.Thread(target=worker, daemon=True) for _ in range(w)]
[t.start() for t in threads]
def sig(s,f): stop[0] = True
signal.signal(signal.SIGTERM, sig)
signal.signal(signal.SIGINT, sig)
while not stop[0]: time.sleep(0.5)
[t.join(timeout=2) for t in threads]
" &
    BACKGROUND_TRAFFIC_PID=$!
    echo "  [BG] Background traffic started (PID: $BACKGROUND_TRAFFIC_PID)"
}

stop_background_traffic() {
    if [ -n "${BACKGROUND_TRAFFIC_PID:-}" ]; then
        echo "  [BG] Stopping background traffic (PID: $BACKGROUND_TRAFFIC_PID)..."
        kill "$BACKGROUND_TRAFFIC_PID" 2>/dev/null || true
        wait "$BACKGROUND_TRAFFIC_PID" 2>/dev/null || true
        BACKGROUND_TRAFFIC_PID=""
    fi
}
NEPHIO_CONTROLLER_PID_FILE="/tmp/nephio-controller.pid"

stop_nephio_controller() {
    if [ -f "$NEPHIO_CONTROLLER_PID_FILE" ]; then
        local pid
        pid=$(cat "$NEPHIO_CONTROLLER_PID_FILE")
        echo "  Stopping Nephio controller (PID $pid)..."
        kill "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
        rm -f "$NEPHIO_CONTROLLER_PID_FILE"
    fi
}

trap 'stop_background_traffic; stop_nephio_controller' EXIT INT TERM

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

# Deploy native mitigations — NetworkPolicies ONLY, deliberately no HPAs/ResourceQuotas.
# Without autoscaling, pods still get overwhelmed under heavy attack load,
# which is the key differentiator vs KubeDDoS which adds HPAs + PriorityClasses.
deploy_native() {
    echo -e "${CYAN}Deploying Native Kubernetes mitigations (NetworkPolicies only)...${NC}"
    echo "  Note: intentionally no HPAs — pods cannot scale to absorb load"
    
    kubectl apply -f "$PROJECT_ROOT/mitigation/kubernetes-native/network-policies/" 2>&1
    
    echo "Waiting 20s for NetworkPolicies to activate..."
    sleep 20
}

# Deploy KubeDDoS (Nephio) mitigations.
# Removes native-only policies first, then applies the full Nephio stack:
# PriorityClasses + NetworkPolicies + ResourceQuotas + HPAs.
# HPAs allow pods to scale under load; PriorityClasses ensure critical services
# (payment, orders) are scheduled first when resources are constrained.
deploy_nephio() {
    echo -e "${CYAN}Deploying KubeDDoS (Nephio) full stack...${NC}"
    echo "  Removing native-only policies to start clean..."
    kubectl delete networkpolicies,hpa,resourcequotas --all -n "$NAMESPACE" \
        --ignore-not-found=true 2>/dev/null || true
    sleep 5
    
    echo "  Applying Nephio stack (PriorityClasses + NetworkPolicies + ResourceQuotas + HPAs)..."
    if [ -f "$PROJECT_ROOT/mitigation/nephio/deploy.sh" ]; then
        bash "$PROJECT_ROOT/mitigation/nephio/deploy.sh" 2>&1 | head -30
    else
        kubectl apply -f "$PROJECT_ROOT/mitigation/nephio/translated/" 2>&1 | head -10
    fi
    
    echo "Waiting 30s for KubeDDoS mitigations to activate and HPAs to stabilise..."
    sleep 30
}

# Deploy Nephio-integrated KubeDDoS.
# A real kopf-based controller watches the DDoSProtection CR and generates
# NetworkPolicies, HPAs, and ResourceQuotas from spec.intent at runtime.
# Contrast with plain KubeDDoS (Phase 3) which applies pre-authored translated/ manifests.
deploy_nephio_integrated() {
    echo -e "${CYAN}Deploying Nephio-integrated KubeDDoS (real controller reconciliation)...${NC}"
    echo "  The controller generates resources FROM the intent — no pre-authored manifests."

    if [ -f "$PROJECT_ROOT/mitigation/nephio/deploy-integrated.sh" ]; then
        bash "$PROJECT_ROOT/mitigation/nephio/deploy-integrated.sh" 2>&1
    else
        echo "  ERROR: deploy-integrated.sh not found"
        return 1
    fi
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

# Start background legitimate traffic — runs across all three phases
start_background_traffic

echo -e "${GREEN}=== PHASE 1: BASELINE (No Mitigations) ===${NC}"
echo "###PHASE:baseline###"
cleanup_all
collect_metrics "baseline-pre" "$RESULTS_DIR/metrics-pre-baseline.json"
run_attack "baseline" "$RESULTS_DIR/attack-baseline.log"
collect_metrics "baseline-post" "$RESULTS_DIR/metrics-post-baseline.json"

echo ""
echo -e "${GREEN}=== PHASE 2: NATIVE KUBERNETES MITIGATIONS (NetworkPolicies only) ===${NC}"
echo "###PHASE:native###"
deploy_native
collect_metrics "native-pre" "$RESULTS_DIR/metrics-pre-native.json"
run_attack "native" "$RESULTS_DIR/attack-native.log"
collect_metrics "native-post" "$RESULTS_DIR/metrics-post-native.json"

echo ""
echo -e "${GREEN}=== PHASE 3: NEPHIO-INTEGRATED (Real controller reconciles intent CR) ===${NC}"
echo "###PHASE:nephio_integrated###"
deploy_nephio_integrated
collect_metrics "nephio_integrated-pre" "$RESULTS_DIR/metrics-pre-nephio_integrated.json"
run_attack "nephio_integrated" "$RESULTS_DIR/attack-nephio_integrated.log"
collect_metrics "nephio_integrated-post" "$RESULTS_DIR/metrics-post-nephio_integrated.json"

stop_nephio_controller
stop_background_traffic

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
for scenario in baseline native nephio_integrated; do
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
echo "###COMPLETE:$RESULTS_DIR###"
echo ""
echo "View summary:"
echo "  cat $RESULTS_DIR/summary.md"
echo ""
