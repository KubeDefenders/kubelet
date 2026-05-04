#!/usr/bin/env bash
#
# Complete DDoS Mitigation Experiment Workflow
# ==============================================
#
# This script runs a complete experimental workflow:
# 1. Run baseline attack (no mitigations) and measure impact
# 2. Deploy native Kubernetes mitigations
# 3. Run attack with native mitigations and measure impact
# 4. Deploy Nephio mitigations
# 5. Run attack with Nephio mitigations and measure impact
# 6. Generate comprehensive comparison report
#
# All metrics are collected automatically and saved for analysis.
#

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
BLUE='\033[0;34m'
NC='\033[0m'

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Configuration
NAMESPACE="${NAMESPACE:-sock-shop}"
ATTACK_DURATION="${ATTACK_DURATION:-180}"  # 3 minutes
ATTACK_WORKERS="${ATTACK_WORKERS:-300}"
ATTACK_RATE="${ATTACK_RATE:-75}"
STABILIZATION_TIME=120
RESULTS_DIR="$PROJECT_ROOT/results/experiments/mitigation-comparison-$(date +%Y%m%d-%H%M%S)"

# Create results directory
mkdir -p "$RESULTS_DIR"

# Log file
LOG_FILE="$RESULTS_DIR/experiment.log"
exec > >(tee -a "$LOG_FILE") 2>&1

#=============================================================================
# HELPER FUNCTIONS
#=============================================================================

print_header() {
    local text="$1"
    echo ""
    echo -e "${MAGENTA}╔═══════════════════════════════════════════════════════════════════╗${NC}"
    printf "${MAGENTA}║${NC} %-65s ${MAGENTA}║${NC}\n" "$text"
    echo -e "${MAGENTA}╚═══════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

print_section() {
    local text="$1"
    echo ""
    echo -e "${CYAN}▶ $text${NC}"
    echo -e "${CYAN}$(printf '═%.0s' {1..70})${NC}"
}

print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_error() {
    echo -e "${RED}✗ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

print_info() {
    echo -e "${BLUE}ℹ $1${NC}"
}

check_prerequisites() {
    print_section "Checking Prerequisites"
    
    local all_ok=true
    
    # Check kubectl
    if command -v kubectl &> /dev/null; then
        print_success "kubectl installed"
    else
        print_error "kubectl not found"
        all_ok=false
    fi
    
    # Check Python
    if command -v python3 &> /dev/null; then
        print_success "python3 installed"
    else
        print_error "python3 not found"
        all_ok=false
    fi
    
    # Check bc for arithmetic
    if command -v bc &> /dev/null; then
        print_success "bc installed"
    else
        print_warning "bc not found (install for accurate metrics: sudo apt-get install bc)"
    fi
    
    # Check Kubernetes cluster
    if kubectl cluster-info &> /dev/null; then
        print_success "Kubernetes cluster accessible"
    else
        print_error "Kubernetes cluster not accessible"
        all_ok=false
    fi
    
    # Check namespace
    if kubectl get namespace "$NAMESPACE" &> /dev/null; then
        print_success "Namespace '$NAMESPACE' exists"
    else
        print_error "Namespace '$NAMESPACE' not found"
        all_ok=false
    fi
    
    # Check target service
    if kubectl get svc front-end -n "$NAMESPACE" &> /dev/null; then
        print_success "Target service 'front-end' found"
    else
        print_error "Target service 'front-end' not found"
        all_ok=false
    fi
    
    # Check attack scripts
    if [ -f "$PROJECT_ROOT/attacks/crossfire_enhanced.py" ]; then
        print_success "Enhanced attack scripts found"
    else
        print_warning "Enhanced attack scripts not found, will use legacy"
    fi
    
    if [ "$all_ok" = false ]; then
        print_error "Prerequisites not met. Please fix errors above."
        exit 1
    fi
    
    echo ""
}

get_target_url() {
    # Use NodePort for external access (realistic attack simulation)
    local minikube_ip=$(minikube ip)
    local node_port=$(kubectl get svc -n "$NAMESPACE" front-end -o jsonpath='{.spec.ports[0].nodePort}')
    
    if [ -z "$node_port" ]; then
        print_error "Could not get NodePort for front-end service"
        return 1
    fi
    
    local target_url="http://${minikube_ip}:${node_port}"
    
    # Test connectivity
    if curl -s -o /dev/null -w "%{http_code}" --connect-timeout 2 "$target_url" | grep -q "200\|301\|302"; then
        echo "$target_url"
        return 0
    else
        print_error "Could not connect to $target_url"
        return 1
    fi
}

collect_metrics() {
    local label="$1"
    local output_file="$2"
    
    print_info "Collecting metrics: $label"
    
    local metrics_json="{"
    
    # Timestamp
    metrics_json+="\"timestamp\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\","
    metrics_json+="\"label\":\"$label\","
    
    # Pod count
    local pod_count=$(kubectl get pods -n "$NAMESPACE" --field-selector=status.phase=Running --no-headers | wc -l)
    metrics_json+="\"pods\":$pod_count,"
    
    # CPU usage (total)
    local cpu_usage=$(kubectl top pods -n "$NAMESPACE" --no-headers 2>/dev/null | awk '{gsub(/m/,"",$2); sum+=$2} END {print int(sum)}')
    cpu_usage=${cpu_usage:-0}
    metrics_json+="\"cpu_millicores\":$cpu_usage,"
    
    # Memory usage (total)
    local mem_usage=$(kubectl top pods -n "$NAMESPACE" --no-headers 2>/dev/null | awk '{gsub(/Mi/,"",$3); sum+=$3} END {print int(sum)}')
    mem_usage=${mem_usage:-0}
    metrics_json+="\"memory_mb\":$mem_usage,"
    
    # HPAs
    local hpa_count=$(kubectl get hpa -n "$NAMESPACE" --no-headers 2>/dev/null | wc -l)
    metrics_json+="\"hpa_count\":$hpa_count,"
    
    # NetworkPolicies
    local netpol_count=$(kubectl get networkpolicies -n "$NAMESPACE" --no-headers 2>/dev/null | wc -l)
    metrics_json+="\"network_policies\":$netpol_count,"
    
    # Resource Quotas
    local quota_count=$(kubectl get resourcequotas -n "$NAMESPACE" --no-headers 2>/dev/null | wc -l)
    metrics_json+="\"resource_quotas\":$quota_count,"
    
    # Nephio features (check for nephio labels)
    local nephio_resources=$(kubectl get all -n "$NAMESPACE" -l nephio.org/managed=true --no-headers 2>/dev/null | wc -l)
    metrics_json+="\"nephio_managed_resources\":$nephio_resources,"
    
    # Response time (if target URL provided)
    if [ -n "$TARGET_URL" ]; then
        local response_time=$(curl -o /dev/null -s -w '%{time_total}' --connect-timeout 5 --max-time 10 "$TARGET_URL" 2>/dev/null || echo "0")
        # Validate and sanitize response_time
        if ! [[ "$response_time" =~ ^[0-9.]+$ ]]; then
            response_time="0"
        fi
        # Convert to milliseconds with fallback (use python3 instead of bc)
        response_time=$(python3 -c "print(int(float('${response_time}') * 1000))" 2>/dev/null || echo "0")
        # Final validation
        if [ -z "$response_time" ] || ! [[ "$response_time" =~ ^[0-9]+$ ]]; then
            response_time="0"
        fi
        metrics_json+="\"response_time_ms\":$response_time,"
        
        # HTTP status
        local http_status=$(curl -o /dev/null -s -w '%{http_code}' --connect-timeout 5 --max-time 10 "$TARGET_URL" 2>/dev/null || echo "0")
        # Validate http_status
        if ! [[ "$http_status" =~ ^[0-9]+$ ]]; then
            http_status="0"
        fi
        metrics_json+="\"http_status\":$http_status"
    else
        metrics_json+="\"response_time_ms\":0,"
        metrics_json+="\"http_status\":0"
    fi
    
    metrics_json+="}"
    
    # Save to file with validation
    if ! echo "$metrics_json" | python3 -m json.tool > "$output_file" 2>/dev/null; then
        # If JSON validation fails, write raw JSON
        echo "$metrics_json" > "$output_file"
        print_warning "Metrics JSON validation failed for $phase, saved raw JSON"
    fi
    
    print_success "Metrics saved to: $(basename "$output_file")"
}

run_attack() {
    local attack_name="$1"
    local output_file="$2"
    
    print_info "Launching attack: $attack_name"
    print_info "Duration: ${ATTACK_DURATION}s | Workers: ${ATTACK_WORKERS} | Rate: ${ATTACK_RATE} req/s/worker"
    
    cd "$PROJECT_ROOT/attacks"
    
    # Try enhanced attack first
    if [ -f "crossfire_enhanced.py" ] && [ -f "target_adapter.py" ]; then
        print_info "Using enhanced crossfire attack..."
        
        # Discover endpoints if not already done
        if [ ! -f "discovered-endpoints.json" ]; then
            print_info "Discovering endpoints..."
            python3 endpoint-discovery.py \
                --target "$TARGET_URL" \
                --max-depth 2 \
                --output discovered-endpoints.json \
                > /dev/null 2>&1 || print_warning "Endpoint discovery failed, continuing anyway"
        fi
        
        # Run enhanced attack (non-interactive mode)
        timeout $((ATTACK_DURATION + 30)) python3 crossfire_enhanced.py \
            --url "$TARGET_URL" \
            --duration "$ATTACK_DURATION" \
            --workers "$ATTACK_WORKERS" \
            --rate "$ATTACK_RATE" \
            --mode adaptive \
            --pattern burst \
            $([ -f "discovered-endpoints.json" ] && echo "--discovery-file discovered-endpoints.json") \
            > "$output_file" 2>&1 &
        
        local attack_pid=$!
        
        # Monitor attack
        print_info "Attack running (PID: $attack_pid)..."
        sleep 10
        print_info "[10s] Attack in progress..."
        sleep 10
        print_info "[20s] Attack in progress..."
        sleep 10
        print_info "[30s] Attack in progress..."
        
        # Wait for attack to complete
        wait $attack_pid 2>/dev/null || true
        
        print_success "Attack completed"
        
    elif [ -f "crossfire-app-level.py" ]; then
        print_warning "Enhanced attacks not found, using legacy crossfire-app-level.py"
        
        # Run legacy attack
        echo "" | timeout $((ATTACK_DURATION + 30)) python3 crossfire-app-level.py \
            --url "$TARGET_URL" \
            --duration "$ATTACK_DURATION" \
            --workers "$ATTACK_WORKERS" \
            --rate "$ATTACK_RATE" \
            > "$output_file" 2>&1 &
        
        local attack_pid=$!
        
        print_info "Attack running (PID: $attack_pid)..."
        wait $attack_pid 2>/dev/null || true
        
        print_success "Attack completed"
    else
        print_error "No attack scripts found"
        return 1
    fi
    
    cd - > /dev/null
}

deploy_native_mitigations() {
    print_section "Deploying Native Kubernetes Mitigations"
    
    local deploy_script="$PROJECT_ROOT/scripts/mitigation/deploy-native-baseline.sh"
    
    if [ -f "$deploy_script" ]; then
        print_info "Running native mitigation deployment script..."
        bash "$deploy_script" 2>&1 | tee "$RESULTS_DIR/native-deploy.log"
        print_success "Native mitigations deployed"
    else
        print_warning "Native deployment script not found, deploying manually..."
        
        # Deploy manually from mitigation directory
        local mitigation_dir="$PROJECT_ROOT/mitigation/kubernetes-native"
        
        if [ -d "$mitigation_dir" ]; then
            # Priority classes
            kubectl apply -f "$mitigation_dir/resource-quotas/priority-classes.yaml" 2>&1 | tee -a "$RESULTS_DIR/native-deploy.log"
            
            # Network policies
            kubectl apply -f "$mitigation_dir/network-policies/" 2>&1 | tee -a "$RESULTS_DIR/native-deploy.log"
            
            # Resource quotas
            kubectl apply -f "$mitigation_dir/resource-quotas/" 2>&1 | tee -a "$RESULTS_DIR/native-deploy.log"
            
            # Autoscaling
            kubectl apply -f "$mitigation_dir/autoscaling/" 2>&1 | tee -a "$RESULTS_DIR/native-deploy.log"
            
            # Pod disruption budgets
            kubectl apply -f "$mitigation_dir/pod-disruption-budgets/" 2>&1 | tee -a "$RESULTS_DIR/native-deploy.log"
            
            print_success "Native mitigations deployed manually"
        else
            print_error "Mitigation directory not found: $mitigation_dir"
            return 1
        fi
    fi
    
    print_info "Waiting ${STABILIZATION_TIME}s for mitigations to stabilize..."
    sleep "$STABILIZATION_TIME"
}

deploy_nephio_mitigations() {
    print_section "Deploying Nephio Mitigations"
    
    local deploy_script="$PROJECT_ROOT/mitigation/nephio/deploy.sh"
    
    if [ -f "$deploy_script" ]; then
        print_info "Running Nephio mitigation deployment script..."
        bash "$deploy_script" 2>&1 | tee "$RESULTS_DIR/nephio-deploy.log"
        print_success "Nephio mitigations deployed"
    else
        print_error "Nephio deployment script not found: $deploy_script"
        return 1
    fi
    
    print_info "Waiting ${STABILIZATION_TIME}s for Nephio mitigations to stabilize..."
    sleep "$STABILIZATION_TIME"
}

cleanup_mitigations() {
    local mitigation_type="$1"
    
    print_info "Cleaning up $mitigation_type mitigations..."
    
    if [ "$mitigation_type" = "native" ]; then
        kubectl delete hpa --all -n "$NAMESPACE" --ignore-not-found=true 2>/dev/null || true
        kubectl delete networkpolicies --all -n "$NAMESPACE" --ignore-not-found=true 2>/dev/null || true
        kubectl delete resourcequotas --all -n "$NAMESPACE" --ignore-not-found=true 2>/dev/null || true
        kubectl delete pdb --all -n "$NAMESPACE" --ignore-not-found=true 2>/dev/null || true
        kubectl delete priorityclasses -l app=sock-shop --ignore-not-found=true 2>/dev/null || true
    elif [ "$mitigation_type" = "nephio" ]; then
        kubectl delete -l nephio.org/managed=true -n "$NAMESPACE" --ignore-not-found=true 2>/dev/null || true
        kubectl delete priorityclasses -l nephio.org/managed=true --ignore-not-found=true 2>/dev/null || true
    fi
    
    print_success "$mitigation_type mitigations cleaned up"
    sleep 5
}

generate_report() {
    print_section "Generating Comparison Report"
    
    local report_file="$RESULTS_DIR/comparison-report.md"
    
    cat > "$report_file" <<EOF
# DDoS Mitigation Comparison Report

**Generated:** $(date)
**Namespace:** $NAMESPACE
**Attack Duration:** ${ATTACK_DURATION}s
**Attack Configuration:** ${ATTACK_WORKERS} workers @ ${ATTACK_RATE} req/s/worker

---

## Experiment Overview

This experiment evaluates the effectiveness of DDoS mitigations by running identical attacks under three scenarios:

1. **Baseline** - No mitigations (baseline impact)
2. **Native Kubernetes** - HPAs, NetworkPolicies, ResourceQuotas, PDBs
3. **Nephio** - Enhanced mitigations with Nephio orchestration

---

## Results Summary

EOF
    
    # Parse metrics files and add to report
    for scenario in baseline native-mitigations nephio-mitigations; do
        echo "### ${scenario^}" >> "$report_file"
        echo "" >> "$report_file"
        
        if [ -f "$RESULTS_DIR/metrics-pre-$scenario.json" ] && [ -f "$RESULTS_DIR/metrics-during-$scenario.json" ]; then
            echo "**Pre-Attack Metrics:**" >> "$report_file"
            echo '```json' >> "$report_file"
            cat "$RESULTS_DIR/metrics-pre-$scenario.json" >> "$report_file"
            echo '```' >> "$report_file"
            echo "" >> "$report_file"
            
            echo "**During Attack Metrics:**" >> "$report_file"
            echo '```json' >> "$report_file"
            cat "$RESULTS_DIR/metrics-during-$scenario.json" >> "$report_file"
            echo '```' >> "$report_file"
            echo "" >> "$report_file"
            
            echo "**Post-Attack Metrics:**" >> "$report_file"
            echo '```json' >> "$report_file"
            cat "$RESULTS_DIR/metrics-post-$scenario.json" >> "$report_file"
            echo '```' >> "$report_file"
            echo "" >> "$report_file"
        fi
    done
    
    cat >> "$report_file" <<EOF

---

## Attack Logs

### Baseline Attack
\`\`\`
$(cat "$RESULTS_DIR/attack-baseline.log" 2>/dev/null | head -100 || echo "Log not available")
\`\`\`

### With Native Mitigations
\`\`\`
$(cat "$RESULTS_DIR/attack-native-mitigations.log" 2>/dev/null | head -100 || echo "Log not available")
\`\`\`

### With Nephio Mitigations
\`\`\`
$(cat "$RESULTS_DIR/attack-nephio-mitigations.log" 2>/dev/null | head -100 || echo "Log not available")
\`\`\`

---

## Conclusion

Compare the metrics above to evaluate:

1. **Response Time Degradation**: How much did latency increase under attack?
2. **Pod Scaling**: Did HPAs scale pods appropriately?
3. **Resource Usage**: How did CPU/memory usage change?
4. **Service Availability**: Did the service remain responsive?
5. **Mitigation Effectiveness**: How much did each mitigation layer reduce impact?

**Key Metrics to Compare:**
- Response time increase (ms)
- Pod count increase
- CPU usage increase (millicores)
- HTTP error rates
- Service availability

---

## Files Generated

- \`experiment.log\` - Complete experiment log
- \`metrics-*.json\` - Metrics snapshots for each phase
- \`attack-*.log\` - Attack execution logs
- \`*-deploy.log\` - Mitigation deployment logs
- \`comparison-report.md\` - This report

EOF
    
    print_success "Report generated: $report_file"
}

#=============================================================================
# MAIN EXPERIMENT WORKFLOW
#=============================================================================

main() {
    print_header "DDOS MITIGATION EFFECTIVENESS EXPERIMENT"
    
    echo -e "${CYAN}This experiment will:${NC}"
    echo "  1. Run baseline attack (no mitigations)"
    echo "  2. Deploy native Kubernetes mitigations"
    echo "  3. Run attack with native mitigations"
    echo "  4. Deploy Nephio mitigations"
    echo "  5. Run attack with Nephio mitigations"
    echo "  6. Generate comparison report"
    echo ""
    echo -e "${YELLOW}Attack Configuration:${NC}"
    echo "  Duration: ${ATTACK_DURATION}s"
    echo "  Workers: ${ATTACK_WORKERS}"
    echo "  Rate: ${ATTACK_RATE} req/s/worker"
    echo "  Total Rate: $((ATTACK_WORKERS * ATTACK_RATE)) req/s"
    echo ""
    echo -e "${YELLOW}Results Directory:${NC} $RESULTS_DIR"
    echo ""
    
    # Check prerequisites
    check_prerequisites
    
    # Get target URL
    print_section "Configuring Target"
    TARGET_URL=$(get_target_url)
    if [ $? -ne 0 ]; then
        print_error "Failed to get target URL"
        exit 1
    fi
    print_success "Target URL: $TARGET_URL"
    
    # Add resource requests to enable HPAs
    print_section "Adding Resource Requests"
    print_info "Patching deployments with resource requests for HPA..."
    bash "$PROJECT_ROOT/scripts/mitigation/add-resource-requests.sh" > "$RESULTS_DIR/resource-requests.log" 2>&1
    if [ $? -eq 0 ]; then
        print_success "Resource requests added"
    else
        print_warning "Resource requests script had issues, check log"
    fi
    sleep 10  # Wait for pods to restart
    
    #=========================================================================
    # PHASE 1: BASELINE (No Mitigations)
    #=========================================================================
    
    print_header "PHASE 1: BASELINE ATTACK (No Mitigations)"
    
    # Ensure no mitigations are present
    print_info "Cleaning up any existing mitigations..."
    cleanup_mitigations "native" 2>/dev/null || true
    cleanup_mitigations "nephio" 2>/dev/null || true
    sleep 10
    
    # Collect pre-attack metrics
    collect_metrics "baseline-pre-attack" "$RESULTS_DIR/metrics-pre-baseline.json"
    
    # Run attack
    run_attack "baseline" "$RESULTS_DIR/attack-baseline.log" &
    ATTACK_PID=$!
    
    # Wait for attack to reach peak (midpoint)
    sleep $((ATTACK_DURATION / 2))
    
    # Collect during-attack metrics at peak
    collect_metrics "baseline-during-attack" "$RESULTS_DIR/metrics-during-baseline.json"
    
    # Wait for attack to complete
    wait $ATTACK_PID || true
    
    # Wait for stabilization
    print_info "Waiting ${STABILIZATION_TIME}s for system to stabilize..."
    sleep "$STABILIZATION_TIME"
    
    # Collect post-attack metrics
    collect_metrics "baseline-post-attack" "$RESULTS_DIR/metrics-post-baseline.json"
    
    print_success "Phase 1 complete"
    
    #=========================================================================
    # PHASE 2: NATIVE KUBERNETES MITIGATIONS
    #=========================================================================
    
    print_header "PHASE 2: ATTACK WITH NATIVE KUBERNETES MITIGATIONS"
    
    # Deploy mitigations
    deploy_native_mitigations
    
    # Collect pre-attack metrics
    collect_metrics "native-pre-attack" "$RESULTS_DIR/metrics-pre-native-mitigations.json"
    
    # Run attack
    run_attack "native-mitigations" "$RESULTS_DIR/attack-native-mitigations.log" &
    ATTACK_PID=$!
    
    # Wait for attack to reach peak (midpoint)
    sleep $((ATTACK_DURATION / 2))
    
    # Collect during-attack metrics at peak
    collect_metrics "native-during-attack" "$RESULTS_DIR/metrics-during-native-mitigations.json"
    
    # Wait for attack to complete
    wait $ATTACK_PID || true
    
    # Wait for stabilization
    print_info "Waiting ${STABILIZATION_TIME}s for system to stabilize..."
    sleep "$STABILIZATION_TIME"
    
    # Collect post-attack metrics
    collect_metrics "native-post-attack" "$RESULTS_DIR/metrics-post-native-mitigations.json"
    
    print_success "Phase 2 complete"
    
    #=========================================================================
    # PHASE 3: NEPHIO MITIGATIONS
    #=========================================================================
    
    print_header "PHASE 3: ATTACK WITH NEPHIO MITIGATIONS"
    
    # Deploy Nephio mitigations (adds to native)
    deploy_nephio_mitigations
    
    # Collect pre-attack metrics
    collect_metrics "nephio-pre-attack" "$RESULTS_DIR/metrics-pre-nephio-mitigations.json"
    
    # Run attack
    run_attack "nephio-mitigations" "$RESULTS_DIR/attack-nephio-mitigations.log" &
    ATTACK_PID=$!
    
    # Wait for attack to reach peak (midpoint)
    sleep $((ATTACK_DURATION / 2))
    
    # Collect during-attack metrics at peak
    collect_metrics "nephio-during-attack" "$RESULTS_DIR/metrics-during-nephio-mitigations.json"
    
    # Wait for attack to complete
    wait $ATTACK_PID || true
    
    # Wait for stabilization
    print_info "Waiting ${STABILIZATION_TIME}s for system to stabilize..."
    sleep "$STABILIZATION_TIME"
    
    # Collect post-attack metrics
    collect_metrics "nephio-post-attack" "$RESULTS_DIR/metrics-post-nephio-mitigations.json"
    
    print_success "Phase 3 complete"
    
    #=========================================================================
    # GENERATE REPORT
    #=========================================================================
    
    generate_report
    
    #=========================================================================
    # CLEANUP (Optional)
    #=========================================================================
    
    print_header "EXPERIMENT COMPLETE"
    
    echo ""
    echo -e "${GREEN}All phases completed successfully!${NC}"
    echo ""
    echo -e "${CYAN}Results saved to:${NC} $RESULTS_DIR"
    echo ""
    echo -e "${CYAN}Generated files:${NC}"
    ls -lh "$RESULTS_DIR"
    echo ""
    echo -e "${YELLOW}View the report:${NC}"
    echo "  cat $RESULTS_DIR/comparison-report.md"
    echo ""
    echo -e "${YELLOW}Optional cleanup (remove mitigations):${NC}"
    echo "  kubectl delete hpa,networkpolicies,resourcequotas,pdb --all -n $NAMESPACE"
    echo "  kubectl delete priorityclasses -l nephio.org/managed=true"
    echo ""
}

# Run main workflow
main "$@"
