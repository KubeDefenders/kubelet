# Quick Start: Run Complete DDoS Mitigation Experiment

## Overview

This guide shows you how to run a complete experiment that:
1. Attacks your application with **no mitigations** (baseline)
2. Deploys **native Kubernetes mitigations** and attacks again
3. Deploys **Nephio mitigations** and attacks again
4. Compares all three scenarios with detailed metrics

**Total Time:** ~10 minutes

---

## Prerequisites Check

Before starting, ensure:

```bash
# 1. Kubernetes cluster is running
kubectl cluster-info

# 2. Sock Shop is deployed
kubectl get pods -n sock-shop

# 3. You're in the project directory
cd /home/spuggle/dev/ddos
```

---

## Run the Experiment

### Option 1: Default Configuration (Recommended First Run)

```bash
./scripts/workflows/run-full-mitigation-experiment.sh
```

**Configuration:**
- Attack Duration: 60 seconds per phase
- Workers: 50 concurrent
- Rate: 50 requests/second per worker
- Total Rate: 2,500 requests/second

**Time:** ~8-10 minutes

### Option 2: Quick Test (Faster)

```bash
ATTACK_DURATION=30 ATTACK_WORKERS=20 ATTACK_RATE=30 \
./scripts/workflows/run-full-mitigation-experiment.sh
```

**Configuration:**
- Attack Duration: 30 seconds per phase
- Workers: 20 concurrent
- Rate: 30 requests/second per worker
- Total Rate: 600 requests/second

**Time:** ~5-6 minutes

### Option 3: Aggressive Test (Stress Test)

```bash
ATTACK_DURATION=120 ATTACK_WORKERS=100 ATTACK_RATE=200 \
./scripts/workflows/run-full-mitigation-experiment.sh
```

**Configuration:**
- Attack Duration: 120 seconds per phase
- Workers: 100 concurrent
- Rate: 200 requests/second per worker
- Total Rate: 20,000 requests/second

**Time:** ~15-20 minutes

---

## What You'll See

### Console Output

The script will display:

```
╔═══════════════════════════════════════════════════════════════════╗
║              DDOS MITIGATION EFFECTIVENESS EXPERIMENT             ║
╚═══════════════════════════════════════════════════════════════════╝

This experiment will:
  1. Run baseline attack (no mitigations)
  2. Deploy native Kubernetes mitigations
  3. Run attack with native mitigations
  4. Deploy Nephio mitigations
  5. Run attack with Nephio mitigations
  6. Generate comparison report

...

✓ Prerequisites met
✓ Target URL: http://localhost:8080

═══════════════════════════════════════════════════════════════════
                    PHASE 1: BASELINE ATTACK                        
═══════════════════════════════════════════════════════════════════

ℹ Cleaning up any existing mitigations...
✓ native mitigations cleaned up
✓ nephio mitigations cleaned up

ℹ Collecting metrics: baseline-pre-attack
✓ Metrics saved to: metrics-pre-baseline.json

ℹ Launching attack: baseline
ℹ Duration: 60s | Workers: 50 | Rate: 50 req/s/worker
ℹ Using enhanced crossfire attack...
ℹ Attack running (PID: 12345)...
ℹ [10s] Attack in progress...
ℹ [20s] Attack in progress...
ℹ [30s] Attack in progress...
✓ Attack completed

ℹ Collecting metrics: baseline-during-attack
✓ Metrics saved to: metrics-during-baseline.json

...
```

### Progress Indicators

- ✓ = Success (green)
- ✗ = Error (red)
- ⚠ = Warning (yellow)
- ℹ = Info (blue)
- ▶ = Section header (cyan)

---

## View Results

### Immediately After Completion

```bash
# View the comparison report
cat results/experiments/mitigation-comparison-*/comparison-report.md

# Or open in your editor
code results/experiments/mitigation-comparison-*/comparison-report.md
```

### Explore Individual Metrics

```bash
# View all metrics as JSON
cat results/experiments/mitigation-comparison-*/metrics-*.json | jq .

# View just baseline metrics
jq . results/experiments/mitigation-comparison-*/metrics-*baseline*.json

# Compare response times
grep response_time_ms results/experiments/mitigation-comparison-*/metrics-during-*.json
```

### View Attack Logs

```bash
# Baseline attack output
cat results/experiments/mitigation-comparison-*/attack-baseline.log

# Native mitigations attack output
cat results/experiments/mitigation-comparison-*/attack-native-mitigations.log

# Nephio mitigations attack output
cat results/experiments/mitigation-comparison-*/attack-nephio-mitigations.log
```

### View Deployment Logs

```bash
# Native mitigation deployment
cat results/experiments/mitigation-comparison-*/native-deploy.log

# Nephio mitigation deployment
cat results/experiments/mitigation-comparison-*/nephio-deploy.log
```

---

## Understanding the Results

### Key Metrics to Compare

1. **Response Time (ms)**
   - Baseline: High increase (500-2000ms)
   - Native: Moderate increase (100-500ms)
   - Nephio: Low increase (50-200ms)

2. **Pod Count**
   - Baseline: No change (no HPAs)
   - Native: +3-10 pods (HPA scaling)
   - Nephio: +3-10 pods (HPA scaling)

3. **CPU Usage (millicores)**
   - Baseline: Spike to limits
   - Native: Distributed across scaled pods
   - Nephio: Well-distributed

4. **HTTP Status**
   - Baseline: May show 503/504 (timeouts)
   - Native: Should remain 200
   - Nephio: Should remain 200

### Example Comparison

```json
// Baseline (during attack)
{
  "response_time_ms": 1845,
  "http_status": 200,
  "pods": 12,
  "cpu_millicores": 3500,
  "hpa_count": 0
}

// Native Mitigations (during attack)
{
  "response_time_ms": 342,
  "http_status": 200,
  "pods": 18,
  "cpu_millicores": 4200,
  "hpa_count": 6
}

// Nephio Mitigations (during attack)
{
  "response_time_ms": 187,
  "http_status": 200,
  "pods": 20,
  "cpu_millicores": 4100,
  "nephio_managed_resources": 12,
  "hpa_count": 6
}
```

**Analysis:**
- Native mitigations reduced response time by **81%** (1845ms → 342ms)
- Nephio further reduced response time by **45%** (342ms → 187ms)
- Both mitigations scaled pods successfully
- Service remained available in all cases

---

## Cleanup After Experiment

The experiment leaves mitigations deployed. To remove them:

```bash
# Remove all mitigations
kubectl delete hpa,networkpolicies,resourcequotas,pdb --all -n sock-shop

# Remove priority classes
kubectl delete priorityclasses -l app=sock-shop
kubectl delete priorityclasses -l nephio.org/managed=true

# Or keep them for production use!
```

---

## Troubleshooting

### Error: "Kubernetes cluster not accessible"

```bash
# Check cluster status
kubectl cluster-info

# If using Minikube
minikube status
minikube start  # if not running
```

### Error: "Namespace sock-shop not found"

```bash
# Deploy Sock Shop first
kubectl create namespace sock-shop
kubectl apply -f target/app/deploy/kubernetes/complete-demo.yaml
```

### Error: "Target service front-end not found"

```bash
# Check service exists
kubectl get svc -n sock-shop front-end

# If missing, redeploy Sock Shop
kubectl apply -f target/app/deploy/kubernetes/complete-demo.yaml
```

### Error: "Could not establish port forward"

```bash
# Kill any existing port forwards
pkill -f "port-forward.*front-end"

# Manually start port forward
kubectl port-forward -n sock-shop svc/front-end 8080:80 &

# Then run experiment again
```

### Error: "Attack scripts not found"

```bash
# Check attack scripts exist
ls -l attacks/crossfire_enhanced.py
ls -l attacks/crossfire-app-level.py

# If missing, you're in wrong directory
cd /home/spuggle/dev/ddos
```

---

## Advanced Usage

### Custom Configuration

Set environment variables:

```bash
export NAMESPACE="my-namespace"      # Target namespace
export ATTACK_DURATION=90            # Attack duration
export ATTACK_WORKERS=75             # Concurrent workers
export ATTACK_RATE=100               # Requests/second/worker

./scripts/workflows/run-full-mitigation-experiment.sh
```

### Parallel Monitoring

Start monitoring before the experiment:

```bash
# Terminal 1: Start ML monitoring
cd detection/ml-detector
python3 continuous_monitor.py --duration 600 &

# Terminal 2: Watch pods
watch -n 2 kubectl get pods -n sock-shop

# Terminal 3: Watch metrics
watch -n 2 kubectl top pods -n sock-shop

# Terminal 4: Run experiment
./scripts/workflows/run-full-mitigation-experiment.sh
```

### Grafana Dashboard

View in real-time:

```bash
# Forward Grafana
kubectl port-forward -n istio-system svc/grafana 3000:3000 &

# Open browser
xdg-open http://localhost:3000  # Linux
open http://localhost:3000       # macOS

# Run experiment in another terminal
./scripts/workflows/run-full-mitigation-experiment.sh
```

---

## Next Steps

1. **Run the experiment:**
   ```bash
   ./scripts/workflows/run-full-mitigation-experiment.sh
   ```

2. **Review results:**
   ```bash
   cat results/experiments/mitigation-comparison-*/comparison-report.md
   ```

3. **Analyze metrics programmatically:**
   ```bash
   # Create custom analysis script
   python3 analyze_results.py results/experiments/mitigation-comparison-*/
   ```

4. **Share results:**
   ```bash
   # Commit to git (if desired)
   git add results/experiments/mitigation-comparison-*/
   git commit -m "DDoS mitigation experiment results"
   ```

---

## Summary

✅ **You now have:**
- A complete, automated experiment workflow
- Baseline attack measurements (no mitigations)
- Native Kubernetes mitigation measurements
- Nephio mitigation measurements
- Comprehensive comparison report
- All metrics saved as JSON for analysis

🎯 **Ready to run:**
```bash
./scripts/workflows/run-full-mitigation-experiment.sh
```

⏱️ **Time required:** ~10 minutes

📊 **Output:** Full comparison report + 18 JSON metrics files + logs

---

**Questions or issues?** Check the detailed documentation:
- [Implementation Analysis](IMPLEMENTATION_ANALYSIS.md)
- [Experiment README](README-experiment.md)
- [Attack Scripts](../../attacks/README.md)
