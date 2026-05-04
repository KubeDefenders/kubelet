# Nephio Crossfire Protection Package

Comprehensive DDoS mitigation package for Nephio, providing both Kubernetes-native protections and Nephio-exclusive advanced features for defending against crossfire attacks.

## Overview

This package implements a **layered defense strategy** combining:

1. **Kubernetes Native Mitigations** - NetworkPolicies, HPA, ResourceQuotas, PDB
2. **Istio Service Mesh** - Rate limiting, circuit breakers, traffic management
3. **Nephio-Exclusive Features** - Multi-cluster coordination, ML-based adaptation, network function chaining

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Ingress Traffic                              │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Layer 1: Network Function Chain (Nephio-Exclusive)            │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐         │
│  │   Traffic    │→ │     Rate     │→ │   Anomaly    │→        │
│  │   Scrubber   │  │   Limiter    │  │   Detector   │         │
│  └──────────────┘  └──────────────┘  └──────────────┘         │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Layer 2: Dynamic Traffic Steering (Nephio-Exclusive)          │
│  ┌──────────────────────────────────────────────────┐          │
│  │  ML-Based Classification:                         │          │
│  │  • Legitimate → Normal Path                      │          │
│  │  • Suspicious → Scrubbing Path                   │          │
│  │  • Attack → Honeypot / Drop                      │          │
│  └──────────────────────────────────────────────────┘          │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Layer 3: Istio Service Mesh                                    │
│  • Rate Limiting (500 req/s)                                   │
│  • Circuit Breakers (100 connections)                          │
│  • Timeout & Retry Policies                                    │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Layer 4: Network Policies                                      │
│  • Decoy Service Isolation                                     │
│  • Critical Path Protection                                    │
│  • Dynamic Attack-Adaptive Policies (Nephio)                  │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Layer 5: Service Pods                                          │
│  • HPA Auto-scaling (2-20 replicas)                            │
│  • Resource Quotas                                             │
│  • Predictive Pre-scaling (Nephio)                            │
│  • Multi-Cluster Distribution (Nephio)                        │
└─────────────────────────────────────────────────────────────────┘
```

## Components

### Kubernetes Native Features

#### 1. Network Policies
- **Decoy Service Isolation**: Isolate frequently-attacked services (catalogue, cart)
- **Critical Service Protection**: Protect payment, orders, shipping services
- **Dynamic Policies** (Nephio): Automatically adjust based on attack detection

#### 2. Autoscaling (HPA)
- **Front-end**: 2-20 replicas, scale at 70% CPU
- **Catalogue** (decoy): 1-30 replicas, aggressive scaling (200% in 15s)
- **Multi-Cluster Coordination** (Nephio): Distribute load across clusters
- **Predictive Autoscaling** (Nephio): Pre-scale based on ML predictions

#### 3. Resource Quotas
- **Namespace-level**: 20 CPU, 32Gi memory
- **Priority-based**: Critical services get guaranteed resources
- **Dynamic Adjustment** (Nephio): Increase quotas during attacks

### Nephio-Exclusive Features

#### 1. Network Function Chaining
Chain mitigation functions in sequence:
- Traffic Scrubber → Rate Limiter → Anomaly Detector → Traffic Shaper

#### 2. Dynamic Traffic Steering
ML-based traffic classification and routing:
- **Legitimate traffic**: Normal path
- **Suspicious traffic**: Scrubbing path
- **Attack traffic**: Honeypot or drop

#### 3. Multi-Cluster Coordination
- **Capacity Requests**: Automatic cluster expansion during attacks
- **Traffic Distribution**: Weighted distribution across clusters (40/30/30)
- **Spillover**: Automatic failover when cluster capacity reached

#### 4. Adaptive Rate Limiting
- **ML-Based**: Adjust limits based on traffic patterns
- **Anomaly-Responsive**: Tighten limits when anomaly score > 0.8
- **Distributed**: Coordinate rate limits across clusters

#### 5. Predictive Autoscaling
- **60-second prediction window**: Scale before attack hits
- **ML-powered**: Use crossfire predictor model
- **Auto-rollback**: Revert if prediction was false

#### 6. Attack Pattern Learning
- **Federated Learning**: Share attack patterns across clusters
- **Auto-apply**: Automatically apply new defenses
- **Continuous Improvement**: Learn from each attack

### Monitoring & Alerting

#### Metrics Collected
- Kubernetes: Pod metrics, HPA, resource quotas, network policies
- Istio: Request rate, error rate, latency, circuit breaker status
- Nephio: Capacity utilization, NF chain latency, spillover events
- ML Detector: Anomaly score, attack classification, effectiveness

#### Alert Rules
- **CrossfireAttackDetected**: Anomaly score > 0.8 for 30s
- **RateLimitExceeded**: Request rate > 5000 req/s
- **ServiceDegradation**: Error rate > 5% for 60s
- **HPAMaxReplicas**: HPA at maximum, trigger cluster expansion

## Deployment

### Prerequisites

1. **Kubernetes cluster** with kubectl configured
2. **kpt** (optional but recommended): `brew install kpt` or download from https://kpt.dev
3. **Nephio management cluster** (for full Nephio features)
4. **Istio** installed for service mesh features

### Quick Start

```bash
# Deploy the package
cd mitigations/nephio/packages/crossfire-protection-package
./deploy.sh

# Verify deployment
./verify.sh

# Monitor status
watch -n 2 'kubectl get pods,hpa,networkpolicies -n sock-shop'
```

### Custom Configuration

Set environment variables to customize deployment:

```bash
# Target namespace
export TARGET_NAMESPACE=sock-shop

# Cluster name
export CLUSTER_NAME=edge-cluster-01

# Protection level (low, medium, high, maximum)
export PROTECTION_LEVEL=high

# Deploy
./deploy.sh
```

### Using kpt

```bash
# Clone package
kpt pkg get https://github.com/your-repo/nephio-packages/crossfire-protection crossfire-protection

# Customize
kpt fn eval crossfire-protection \
    --image gcr.io/kpt-fn/set-annotations:v0.1.4 -- \
    target-namespace=my-namespace \
    protection-level=maximum

# Apply
kpt live init crossfire-protection
kpt live apply crossfire-protection
```

## Configuration Parameters

Edit `package-context.yaml` to adjust parameters:

| Parameter | Default | Description |
|-----------|---------|-------------|
| target-namespace | sock-shop | Target namespace |
| cluster-name | edge-cluster-01 | Cluster identifier |
| protection-level | high | Protection level |
| max-rps | 10000 | Maximum requests/second |
| max-connections | 5000 | Maximum concurrent connections |
| max-pods | 100 | Maximum pod count |
| rate-limit-threshold | 500 | Rate limit (req/s) |
| connection-limit | 100 | Connection limit |
| cpu-scale-threshold | 70 | CPU % to trigger scaling |

## Testing

### Test Attack Scenarios

```bash
# Test basic attack (should be mitigated)
cd ../../../attack-simulations
python3 attack.py --target-url http://your-service:30001 --rate 10

# Test enhanced attack (tests mitigation effectiveness)
./enhanced-attacks.sh
# Select option 4 (Multi-Vector Crossfire)

# Monitor mitigation response
kubectl logs -n sock-shop -l app=ml-detector -f
```

### Verify Mitigations

```bash
# Check network policies are blocking
kubectl get networkpolicies -n sock-shop

# Check HPA is scaling
watch kubectl get hpa -n sock-shop

# Check Nephio features
kubectl get ddosprotections,capacityrequests,networkfunctionchains -n sock-shop

# Check metrics
kubectl port-forward -n sock-shop svc/grafana 3000:3000
# Open http://localhost:3000
```

## Comparison: Kubernetes Native vs Nephio

| Feature | Kubernetes Native | Nephio-Enhanced |
|---------|------------------|-----------------|
| Network Policies | ✓ Static rules | ✓ Dynamic, attack-adaptive |
| Autoscaling | ✓ HPA (reactive) | ✓ Predictive, multi-cluster |
| Rate Limiting | ✗ (via Istio) | ✓ Adaptive, ML-based |
| Traffic Steering | ✗ | ✓ ML-based classification |
| Multi-Cluster | ✗ | ✓ Coordinated capacity |
| Network Functions | ✗ | ✓ Chaining, orchestration |
| Attack Learning | ✗ | ✓ Federated learning |
| Resource Quotas | ✓ Static | ✓ Dynamic adjustment |

## Nephio-Exclusive Advantages

1. **Intent-Based Configuration**: Declare desired state, Nephio figures out implementation
2. **Multi-Cluster Orchestration**: Seamlessly distribute load across clusters during attacks
3. **Predictive Defense**: Pre-scale and prepare defenses before attack hits
4. **Network Function Chaining**: Layer multiple mitigation functions automatically
5. **Federated Learning**: Learn from attacks across all clusters, improve defenses globally
6. **Dynamic Adaptation**: Automatically adjust all mitigations based on real-time threat intelligence

## Troubleshooting

### Nephio CRDs Not Found

```bash
# Install Nephio CRDs
kubectl apply -f ../../workload-apis/ddos-protection-crds.yaml
```

### HPA Not Scaling

```bash
# Check metrics-server
kubectl top nodes
kubectl top pods -n sock-shop

# If metrics unavailable, install metrics-server
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
```

### Istio Features Not Working

```bash
# Verify Istio installation
kubectl get pods -n istio-system

# Inject Istio sidecar
kubectl label namespace sock-shop istio-injection=enabled
kubectl rollout restart deployment -n sock-shop
```

### ML Detector Not Running

```bash
# Check ml-detector deployment
kubectl get pods -n sock-shop -l app=ml-detector

# View logs
kubectl logs -n sock-shop -l app=ml-detector

# Deploy if missing
cd ../../../ml-detector
./scripts/deployment/deploy.sh
```

## References

- **Nephio Documentation**: https://nephio.org/docs
- **kpt Documentation**: https://kpt.dev
- **Istio Documentation**: https://istio.io/docs
- **Kubernetes NetworkPolicies**: https://kubernetes.io/docs/concepts/services-networking/network-policies/
- **HPA Documentation**: https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/

## License

See top-level LICENSE file.
