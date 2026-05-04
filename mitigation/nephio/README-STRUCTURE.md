# Nephio Mitigations - Folder Structure

This directory contains Nephio-enhanced DDoS protection mitigations, properly separated into conceptual and operational components.

## Directory Structure

```
nephio/
├── README.md                          # This file
├── deploy.sh                          # Simple deployment script (USE THIS)
├── deploy-nephio-translated.sh       # OLD - embedded configs (deprecated)
│
├── packages/                          # Nephio conceptual packages (CRD-based)
│   ├── crossfire-protection-package/
│   │   ├── autoscaling.yaml          # Uses workload.nephio.org/v1alpha1 (conceptual)
│   │   ├── network-policies.yaml
│   │   └── resource-quotas.yaml
│   └── ddos-mitigation-base/
│
├── translated/                        # K8s-native YAML files (OPERATIONAL)
│   ├── autoscaling.yaml              # Standard autoscaling/v2 (works)
│   ├── network-policies.yaml         # Standard networking.k8s.io/v1 (works)
│   ├── resource-quotas.yaml          # Standard v1 ResourceQuota (works)
│   └── priority-classes.yaml         # Standard scheduling.k8s.io/v1 (works)
│
└── workload-apis/                     # CRD definitions (for reference)
    └── ddos-protection-crds.yaml
```

## Key Differences

### 1. Native Kubernetes vs Nephio-Enhanced

**Native K8s** (`/mitigations/kubernetes-native/`):
- Basic autoscaling, network policies, quotas
- Minimal labels
- Standard configurations

**Nephio-Enhanced** (`/mitigations/nephio/translated/`):
- Same K8s resources but with:
  - `nephio.org/managed=true` labels
  - `nephio.org/intent` annotations (AutoScaling, NetworkPolicy, etc.)
  - `nephio.org/capacity-request` annotations
  - `nephio.org/slo-availability` annotations
  - Service classification (critical/normal/decoy)
  - Anti-crossfire isolation intents

### 2. Packages vs Translated

**`packages/`** - Conceptual Nephio intents:
- Uses Nephio CRDs (`workload.nephio.org/v1alpha1`)
- Requires Nephio controllers (not implemented)
- For documentation/future reference only

**`translated/`** - Operational K8s resources:
- Uses standard K8s APIs
- Works immediately with kubectl apply
- Contains Nephio-style labels/annotations for compatibility

## Usage

### Deploy Nephio-Enhanced Protection

```bash
cd /home/spuggle/dev/ddos/mitigations/nephio
./deploy.sh
```

### Verify Deployment

```bash
# Check all Nephio-managed resources
kubectl get all,networkpolicies,resourcequotas -n sock-shop -l nephio.org/managed=true

# Check HPAs with Nephio annotations
kubectl describe hpa front-end-hpa-nephio -n sock-shop

# View capacity intents
kubectl get hpa -n sock-shop -l nephio.org/managed=true -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.metadata.annotations.nephio\.org/capacity-request}{"\n"}{end}'
```

### Cleanup

```bash
kubectl delete -f translated/
```

## Why This Structure?

### Problem with Old Approach
The previous `deploy-nephio-translated.sh` embedded all YAML configs inline using `cat <<EOF | kubectl apply`. This caused:
- 500+ line script that should be ~50 lines
- Duplication (configs existed in `packages/` but were recreated in script)
- Maintenance nightmare (changes in multiple places)
- Hard to version control individual resources

### New Approach Benefits
- **Separation of Concerns**: Script just applies files, configs are separate
- **No Duplication**: Single source of truth for each resource
- **Easy Maintenance**: Change YAML directly, script never needs updating
- **Version Control**: Track individual resource changes
- **Clarity**: See exactly what gets deployed (YAML files)

## What Makes This "Nephio"?

Even though we use standard K8s resources, this is Nephio-enhanced because:

1. **Intent-Based Labels**: Resources labeled with intents (AutoScaling, NetworkPolicy)
2. **Capacity Annotations**: Declare capacity requirements (5000 rps)
3. **SLO Annotations**: Service-level objectives (99.9% availability)
4. **Service Classification**: Critical/Normal/Decoy tiers with different policies
5. **Anti-Crossfire Patterns**: Network isolation for decoy services
6. **Managed Tracking**: All resources labeled with `nephio.org/managed=true`

## Testing

### Scenario 1: No Mitigation
```bash
cd /home/spuggle/dev/ddos/attack-simulations
export TARGET_URL=$(minikube service front-end -n sock-shop --url)
python3 crossfire-app-level.py --url "$TARGET_URL" --duration 30 --rate 50 --workers 100 --non-interactive
```

### Scenario 2: Native K8s Mitigation
```bash
kubectl apply -f /home/spuggle/dev/ddos/mitigations/kubernetes-native/network-policies/
kubectl apply -f /home/spuggle/dev/ddos/mitigations/kubernetes-native/resource-quotas/
kubectl apply -f /home/spuggle/dev/ddos/mitigations/kubernetes-native/autoscaling/
sleep 30
python3 crossfire-app-level.py --url "$TARGET_URL" --duration 30 --rate 50 --workers 100 --non-interactive
```

### Scenario 3: Nephio-Enhanced Mitigation
```bash
cd /home/spuggle/dev/ddos/mitigations/nephio
./deploy.sh
sleep 30
cd /home/spuggle/dev/ddos/attack-simulations
python3 crossfire-app-level.py --url "$TARGET_URL" --duration 30 --rate 50 --workers 100 --non-interactive
```

## Expected Results

- **Scenario 1**: ~99% failure rate, severe degradation
- **Scenario 2**: ~50-70% failure rate, moderate protection
- **Scenario 3**: ~30-50% failure rate, enhanced protection with service classification

The difference between Scenarios 2 and 3 is primarily in the **management approach** and **service classification**, not just the resources deployed.
