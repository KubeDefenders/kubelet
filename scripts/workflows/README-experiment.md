# Full Mitigation Experiment Workflow

Automated workflow to test DDoS mitigation effectiveness by running attacks in three scenarios:
1. Baseline (no mitigations)
2. Native Kubernetes mitigations (HPAs, NetworkPolicies, etc.)
3. Nephio mitigations (enhanced orchestration)

## Quick Start

```bash
# Run full experiment with default settings
cd /home/spuggle/dev/ddos
./scripts/workflows/run-full-mitigation-experiment.sh
```

## Prerequisites

- Kubernetes cluster running (Minikube, kind, etc.)
- `sock-shop` application deployed in `sock-shop` namespace
- kubectl configured and accessible
- Python 3 with required packages
- Attack scripts in `attacks/` directory

## Configuration

Set environment variables to customize the experiment:

```bash
export NAMESPACE="sock-shop"           # Target namespace (default: sock-shop)
export ATTACK_DURATION=60              # Attack duration in seconds (default: 60)
export ATTACK_WORKERS=50               # Number of concurrent workers (default: 50)
export ATTACK_RATE=50                  # Requests per second per worker (default: 50)

./scripts/workflows/run-full-mitigation-experiment.sh
```

## What It Does

### Phase 1: Baseline Attack
1. Cleans up any existing mitigations
2. Collects pre-attack metrics
3. Runs crossfire attack for specified duration
4. Collects during-attack and post-attack metrics

### Phase 2: Native Kubernetes Mitigations
1. Deploys native Kubernetes mitigations:
   - HorizontalPodAutoscalers (HPAs)
   - NetworkPolicies
   - ResourceQuotas
   - PodDisruptionBudgets
   - PriorityClasses
2. Waits for stabilization (30s)
3. Collects pre-attack metrics
4. Runs identical attack
5. Collects during-attack and post-attack metrics

### Phase 3: Nephio Mitigations
1. Deploys Nephio-enhanced mitigations (adds to native)
2. Waits for stabilization (30s)
3. Collects pre-attack metrics
4. Runs identical attack
5. Collects during-attack and post-attack metrics

### Report Generation
1. Generates comprehensive markdown report
2. Compares all three scenarios
3. Saves all metrics as JSON
4. Saves attack logs for each phase

## Output

All results are saved to:
```
results/experiments/mitigation-comparison-<timestamp>/
├── experiment.log                          # Complete experiment log
├── metrics-pre-baseline.json              # Pre-attack metrics (baseline)
├── metrics-during-baseline.json           # During-attack metrics (baseline)
├── metrics-post-baseline.json             # Post-attack metrics (baseline)
├── attack-baseline.log                    # Attack output (baseline)
├── metrics-pre-native-mitigations.json    # Pre-attack metrics (native)
├── metrics-during-native-mitigations.json # During-attack metrics (native)
├── metrics-post-native-mitigations.json   # Post-attack metrics (native)
├── attack-native-mitigations.log          # Attack output (native)
├── native-deploy.log                      # Native mitigation deployment log
├── metrics-pre-nephio-mitigations.json    # Pre-attack metrics (nephio)
├── metrics-during-nephio-mitigations.json # During-attack metrics (nephio)
├── metrics-post-nephio-mitigations.json   # Post-attack metrics (nephio)
├── attack-nephio-mitigations.log          # Attack output (nephio)
├── nephio-deploy.log                      # Nephio mitigation deployment log
└── comparison-report.md                   # Final comparison report
```

## Metrics Collected

Each metrics snapshot includes:
- **Timestamp**: ISO 8601 format
- **Pod Count**: Number of running pods
- **CPU Usage**: Total millicores across all pods
- **Memory Usage**: Total MB across all pods
- **HPA Count**: Number of HorizontalPodAutoscalers
- **NetworkPolicy Count**: Number of NetworkPolicies
- **ResourceQuota Count**: Number of ResourceQuotas
- **Nephio Resources**: Count of Nephio-managed resources
- **Response Time**: HTTP GET latency in milliseconds
- **HTTP Status**: Response code from target service

## Viewing Results

### View Comparison Report
```bash
cat results/experiments/mitigation-comparison-*/comparison-report.md
```

### View Metrics (JSON)
```bash
cat results/experiments/mitigation-comparison-*/metrics-*.json | jq .
```

### View Attack Logs
```bash
cat results/experiments/mitigation-comparison-*/attack-*.log
```

### View Complete Experiment Log
```bash
cat results/experiments/mitigation-comparison-*/experiment.log
```

## Cleanup

After the experiment, optionally remove mitigations:

```bash
# Remove all mitigations
kubectl delete hpa,networkpolicies,resourcequotas,pdb --all -n sock-shop
kubectl delete priorityclasses -l nephio.org/managed=true

# Or leave them deployed for production use
```

## Advanced Usage

### Custom Attack Configuration
```bash
# Aggressive attack (200,000 req/s total)
ATTACK_WORKERS=200 ATTACK_RATE=1000 ATTACK_DURATION=120 \
./scripts/workflows/run-full-mitigation-experiment.sh
```

### Quick Test (Short Duration)
```bash
# Quick 30-second test
ATTACK_DURATION=30 ATTACK_WORKERS=20 ATTACK_RATE=20 \
./scripts/workflows/run-full-mitigation-experiment.sh
```

### Different Namespace
```bash
# Test against different application
NAMESPACE="my-app" \
./scripts/workflows/run-full-mitigation-experiment.sh
```

## Integration with CI/CD

Add to GitHub Actions or other CI/CD pipeline:

```yaml
- name: Run Mitigation Experiment
  run: |
    export ATTACK_DURATION=30
    export ATTACK_WORKERS=10
    export ATTACK_RATE=10
    ./scripts/workflows/run-full-mitigation-experiment.sh

- name: Upload Results
  uses: actions/upload-artifact@v3
  with:
    name: mitigation-experiment-results
    path: results/experiments/mitigation-comparison-*
```

## Troubleshooting

### Port Forward Issues
If the script cannot connect to the target:
```bash
# Manually start port forward
kubectl port-forward -n sock-shop svc/front-end 8080:80 &

# Then run experiment
./scripts/workflows/run-full-mitigation-experiment.sh
```

### Missing Attack Scripts
Ensure attack scripts are present:
```bash
ls -l attacks/crossfire_enhanced.py
ls -l attacks/target_adapter.py
```

### Mitigation Deployment Failures
Check deployment scripts exist:
```bash
ls -l scripts/mitigation/deploy-native-baseline.sh
ls -l mitigation/nephio/deploy.sh
```

### Metrics Collection Errors
Ensure metrics-server is running:
```bash
kubectl top nodes
kubectl top pods -n sock-shop
```

## See Also

- [Attack Scripts README](../../attacks/README.md)
- [Native Mitigations](../../mitigation/kubernetes-native/)
- [Nephio Mitigations](../../mitigation/nephio/)
- [ML Detection](../../detection/ml-detector/)
