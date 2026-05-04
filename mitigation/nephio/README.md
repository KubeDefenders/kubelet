# Nephio DDoS Mitigation Package

This directory contains Nephio-based implementations for DDoS detection and mitigation using intent-based configuration, automated lifecycle management, and multi-cluster orchestration.

## Nephio Overview

**Nephio** is a Kubernetes-based platform for managing cloud-native Network Functions (CNFs) using GitOps and intent-based automation. It excels at:
- **Intent-based Configuration**: Declare desired state, Nephio figures out how to achieve it
- **Multi-cluster Orchestration**: Manage DDoS mitigations across multiple clusters
- **Automated Lifecycle Management**: Auto-scaling, self-healing, capacity injection
- **Network Function Chaining**: Chain mitigation functions in service mesh
- **Telco-grade Reliability**: Built for 5-nines availability

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Nephio Management Cluster                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │ Porch Server │  │ Config Sync  │  │  PackageRev  │      │
│  │  (Packages)  │  │   (GitOps)   │  │  Controller  │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
└─────────────────────────────────────────────────────────────┘
                            ▼
        ┌───────────────────┴───────────────────┐
        ▼                                       ▼
┌─────────────────┐                   ┌─────────────────┐
│  Workload Edge  │                   │ Workload Cluster│
│   Cluster 1     │                   │      2          │
│ ┌─────────────┐ │                   │ ┌─────────────┐ │
│ │  DDoS PKG   │ │                   │ │  DDoS PKG   │ │
│ │  Deployed   │ │                   │ │  Deployed   │ │
│ └─────────────┘ │                   │ └─────────────┘ │
└─────────────────┘                   └─────────────────┘
```

## Directory Structure

```
nephio/
├── packages/                       # KPT packages for deployment
│   ├── ddos-mitigation-base/       # Base package (common configs)
│   ├── ddos-mitigation-edge/       # Edge cluster variant
│   └── ddos-mitigation-core/       # Core cluster variant
├── workload-apis/                  # Custom Nephio Workload APIs
│   ├── ddos-protection-crd.yaml    # DDoSProtection CRD
│   ├── capacity-request-crd.yaml   # CapacityRequest CRD
│   └── nf-deploy-crd.yaml          # NFDeploy CRD
├── automation/                     # Automation controllers
│   ├── auto-scale-controller.yaml  # Auto-scaling based on attack detection
│   ├── capacity-injector.yaml      # Capacity injection controller
│   └── self-healing-controller.yaml # Self-healing automation
├── network-functions/              # CNF definitions for DDoS mitigation
│   ├── rate-limiter-cnf.yaml       # Rate limiting network function
│   ├── traffic-shaper-cnf.yaml     # Traffic shaping function
│   └── attack-detector-cnf.yaml    # Attack detection function
└── README.md                       # This file
```

## Key Nephio Concepts for DDoS Mitigation

### 1. Intent-Based Configuration
Instead of imperatively defining resources, declare **intents**:

```yaml
apiVersion: workload.nephio.org/v1alpha1
kind: DDoSProtection
metadata:
  name: sock-shop-protection
spec:
  intent:
    targetNamespace: sock-shop
    protectionLevel: high           # high, medium, low
    attackTypes:
    - http-flood
    - syn-flood
    - crossfire-app
    - crossfire-network
    capacity:
      requestsPerSecond: 10000      # Nephio calculates required resources
      concurrentConnections: 5000
    slo:
      availability: 99.9
      latencyP99: 200ms
```

Nephio **automatically provisions**:
- HPA with calculated replica counts
- Rate limiting with appropriate thresholds
- Circuit breakers with SLO-based configs
- Network policies
- Resource quotas

### 2. Capacity Injection
Nephio automatically calculates and injects capacity requirements:

```yaml
apiVersion: req.nephio.org/v1alpha1
kind: CapacityRequest
metadata:
  name: sock-shop-capacity
spec:
  for:
    group: workload.nephio.org
    kind: DDoSProtection
    name: sock-shop-protection
  capacity:
    downlink: 10Gbps
    uplink: 5Gbps
```

### 3. Multi-Cluster Orchestration
Deploy mitigations across edge and core clusters:

```yaml
apiVersion: config.porch.kpt.dev/v1alpha1
kind: PackageRevision
metadata:
  name: ddos-mitigation-edge-v1
spec:
  packageName: ddos-mitigation-edge
  workspaceName: edge-cluster-1
  repository: deployment
  tasks:
  - type: clone
    clone:
      upstream: ddos-mitigation-base
  - type: patch
    patch:
      file: capacity.yaml
      patchType: strategic
      contents: |
        spec:
          capacity:
            downlink: 1Gbps       # Edge: lower capacity
```

### 4. Network Function Chaining
Chain mitigation functions in a service mesh:

```yaml
apiVersion: nf.nephio.org/v1alpha1
kind: NFDeployment
metadata:
  name: ddos-mitigation-chain
spec:
  nfChain:
  - name: attack-detector
    function: attack-detection
    image: ddos-detector:v1
  - name: rate-limiter
    function: rate-limiting
    image: envoy-ratelimit:v1
  - name: traffic-shaper
    function: traffic-shaping
    image: traffic-shaper:v1
  connectivityType: service-mesh
  serviceMesh:
    provider: istio
```

## Nephio vs Native K8s: Key Differences

| Feature | Native K8s + Istio | Nephio |
|---------|-------------------|--------|
| **Configuration Style** | Imperative (YAML per resource) | Declarative (intent-based) |
| **Capacity Planning** | Manual calculation | Automatic capacity injection |
| **Multi-Cluster** | Manual per-cluster configs | Automated orchestration |
| **Self-Healing** | Basic (liveness/readiness probes) | Advanced (intent reconciliation) |
| **GitOps** | Requires external tools (Argo/Flux) | Built-in (Config Sync, Porch) |
| **Network Functions** | Deploy as Pods | First-class NFDeployment API |
| **Lifecycle Management** | Manual upgrades | Automated rollout strategies |

## Deployment Workflow

### 1. Install Nephio Management Cluster
```bash
# Install Nephio CLI
curl -sSL https://github.com/nephio-project/nephio/releases/download/v1.0.0/install.sh | bash

# Bootstrap Nephio management cluster
nephio install management --cluster-name nephio-mgmt
```

### 2. Register Workload Clusters
```bash
# Register edge cluster
nephio cluster register \
  --name edge-cluster-1 \
  --kubeconfig ~/.kube/edge-cluster-1.yaml \
  --cluster-type edge

# Register core cluster
nephio cluster register \
  --name core-cluster-1 \
  --kubeconfig ~/.kube/core-cluster-1.yaml \
  --cluster-type core
```

### 3. Deploy DDoS Mitigation Package
```bash
# Create package deployment
kpt live init packages/ddos-mitigation-base
kpt live apply packages/ddos-mitigation-base --reconcile-timeout=10m

# Nephio automatically:
# - Calculates capacity requirements
# - Selects target clusters
# - Provisions resources
# - Configures network functions
# - Sets up GitOps sync
```

### 4. Declare DDoS Protection Intent
```bash
kubectl apply -f workload-apis/ddos-protection-intent.yaml
# Nephio reconciles and deploys all required mitigations
```

### 5. Monitor and Adjust
```bash
# Check package status
kubectl get packagerevisions -A

# Check deployed workloads
kubectl get nfdeployments -A

# Adjust protection level
kubectl patch ddosprotection sock-shop-protection \
  -p '{"spec":{"intent":{"protectionLevel":"maximum"}}}' --type=merge
# Nephio automatically scales up all mitigations
```

## Nephio Advantages for DDoS Mitigation

### ✅ What Nephio Provides Beyond Native K8s

1. **Automatic Capacity Calculation**
   - Native: Manual HPA/VPA configuration
   - Nephio: Declare SLO, get automatic capacity injection

2. **Intent-Based Abstractions**
   - Native: 50+ YAML files for complete mitigation
   - Nephio: 1 DDoSProtection resource with intent

3. **Multi-Cluster Orchestration**
   - Native: Manual deployment per cluster
   - Nephio: Single intent propagated to all clusters

4. **Network Function Management**
   - Native: Deploy CNFs as regular Pods
   - Nephio: NFDeployment API with lifecycle management

5. **Integrated GitOps**
   - Native: Separate Argo CD/Flux setup
   - Nephio: Built-in Config Sync and Porch

6. **Advanced Self-Healing**
   - Native: Basic pod restarts
   - Nephio: Intent reconciliation (if rate limiting fails, auto-provision alternative)

7. **Telco-Grade Features**
   - Day-2 operations (upgrades, rollbacks)
   - Capacity forecasting
   - SLA management
   - Inter-cluster traffic steering

### ❌ What Nephio Lacks Compared to Native

1. **Maturity**: Nephio is newer, less battle-tested
2. **Ecosystem**: Smaller community, fewer integrations
3. **Learning Curve**: Steeper (KPT, Porch, custom CRDs)
4. **Overhead**: Requires management cluster + controllers
5. **Simplicity**: Native K8s is simpler for small deployments
6. **Debugging**: More layers of abstraction to debug

## When to Use Nephio

**Use Nephio if:**
- ✅ Managing 3+ clusters
- ✅ Need telco-grade reliability
- ✅ Want automated capacity planning
- ✅ Deploying network functions
- ✅ Complex multi-site deployments
- ✅ GitOps is a hard requirement

**Stick with Native K8s if:**
- ❌ Single-cluster deployment
- ❌ Team unfamiliar with Nephio
- ❌ Simple mitigation requirements
- ❌ Quick PoC or development environment

## Integration with Existing ML Detection

Nephio can enhance the existing ML detector:

```yaml
apiVersion: workload.nephio.org/v1alpha1
kind: DDoSProtection
metadata:
  name: ml-enhanced-protection
spec:
  intent:
    targetNamespace: sock-shop
    protectionLevel: adaptive      # Adapts based on ML predictions
  integrations:
    mlDetector:
      enabled: true
      endpoint: http://ml-detector:5000/predict
      actions:
      - when: "confidence > 0.8"
        do: "increase-rate-limits"
      - when: "confidence > 0.95"
        do: "activate-circuit-breakers"
      - when: "attack-type == 'crossfire'"
        do: "isolate-decoy-services"
```

## Next Steps

1. **Deploy Nephio Management Cluster** (see automation/install-nephio.sh)
2. **Create DDoS Mitigation Packages** (see packages/)
3. **Define Workload APIs** (see workload-apis/)
4. **Implement Automation Controllers** (see automation/)
5. **Test Multi-Cluster Deployment**
6. **Compare Performance** with native K8s mitigations

## References

- [Nephio Official Documentation](https://nephio.org/docs)
- [KPT Package Management](https://kpt.dev/)
- [Porch Package Orchestration](https://github.com/nephio-project/porch)
- [Nephio R3 Release](https://wiki.nephio.org/display/HOME/Release+3)
