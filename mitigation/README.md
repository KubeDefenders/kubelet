# DDoS Mitigation Strategies for Kubernetes

This directory contains comprehensive DDoS detection and mitigation techniques, with a focus on **Crossfire attacks** at both application and network levels.

## 📁 Directory Structure

```
mitigations/
├── kubernetes-native/          # Native K8s mitigation techniques
│   ├── network-policies/       # NetworkPolicy-based controls
│   ├── resource-quotas/        # Resource limits and quotas
│   ├── autoscaling/           # HPA, VPA, KEDA configurations
│   └── pod-disruption/        # PDB for availability
├── istio-advanced/            # Advanced Istio configurations
│   ├── rate-limiting/         # Request rate controls
│   ├── circuit-breaking/      # Circuit breaker patterns
│   ├── fault-injection/       # Chaos engineering
│   └── traffic-shifting/      # Gradual rollout controls
├── nephio/                    # Nephio-based mitigations
│   ├── packages/              # Nephio package definitions
│   ├── workload-apis/         # Custom workload resources
│   └── automation/            # Automated deployment
└── comparative-analysis/      # Feature comparison

```

## 🎯 Crossfire Attack Mitigation Focus

### What are Crossfire Attacks?

Crossfire attacks exploit shared network infrastructure by:
1. **Application Level**: Flooding decoy services to saturate links to target
2. **Network Level**: Creating link congestion through distributed traffic

### Our Mitigation Strategy

**Multi-layered Defense**:
1. Network isolation and traffic shaping
2. Resource limits and quotas
3. Intelligent traffic management
4. Automated scaling and failover
5. Service mesh rate limiting

## 🚀 Quick Start

### 1. Apply Kubernetes Native Mitigations
```bash
# Network policies for isolation
kubectl apply -f kubernetes-native/network-policies/

# Resource quotas to prevent resource exhaustion
kubectl apply -f kubernetes-native/resource-quotas/

# Autoscaling for resilience
kubectl apply -f kubernetes-native/autoscaling/

# Pod disruption budgets
kubectl apply -f kubernetes-native/pod-disruption/
```

### 2. Apply Istio Advanced Controls
```bash
# Rate limiting
kubectl apply -f istio-advanced/rate-limiting/

# Circuit breaking
kubectl apply -f istio-advanced/circuit-breaking/

# Traffic management
kubectl apply -f istio-advanced/traffic-shifting/
```

### 3. Deploy Nephio-based Solutions
```bash
# Install Nephio packages
cd nephio/packages/
kpt pkg get https://github.com/nephio-project/catalog.git/packages/ddos-mitigation

# Deploy workload APIs
kubectl apply -f nephio/workload-apis/
```

## 📊 Comparison: Kubernetes Native vs Nephio

### Feature Matrix

| Feature | K8s Native | Istio | Nephio | Best For |
|---------|-----------|-------|--------|----------|
| **Network Policies** | ✅ Full | ✅ Enhanced | ✅ Automated | Network isolation |
| **Rate Limiting** | ❌ No | ✅ Full | ✅ Dynamic | Request control |
| **Circuit Breaking** | ❌ No | ✅ Full | ✅ Automated | Fault tolerance |
| **Autoscaling** | ✅ HPA/VPA | ✅ Custom | ✅ Intent-based | Load adaptation |
| **Resource Quotas** | ✅ Full | ➖ Partial | ✅ Policy-driven | Resource limits |
| **Traffic Shaping** | ➖ Basic | ✅ Advanced | ✅ Declarative | Bandwidth control |
| **Multi-cluster** | ❌ No | ➖ Complex | ✅ Native | Geographic distribution |
| **Intent-based Config** | ❌ No | ❌ No | ✅ Full | Automation |
| **Package Management** | ➖ Helm | ➖ Helm | ✅ KPT | Deployment |
| **Lifecycle Management** | ➖ Manual | ➖ Manual | ✅ Automated | Operations |

### What Nephio Adds

**Unique Capabilities**:
1. **Intent-based Configuration**: Declare desired state, Nephio figures out how
2. **Package Orchestration**: KPT-based package lifecycle management
3. **Multi-cluster Native**: Built for edge and distributed deployments
4. **Network Functions**: Telco-grade network function management
5. **Automated Remediation**: Self-healing based on observed state
6. **Workload APIs**: Higher-level abstractions for common patterns

**What Nephio Lacks** (compared to native):
1. **Maturity**: Kubernetes native features are battle-tested
2. **Simplicity**: Adds complexity for simple use cases
3. **Community Size**: Smaller ecosystem than core K8s
4. **Direct Control**: More abstraction = less fine-grained control
5. **Learning Curve**: Requires understanding of KPT, Porch, etc.

### Overlap Analysis

**High Overlap (70-80%)**:
- Resource management (quotas, limits, requests)
- Pod placement (affinity, anti-affinity, taints)
- Basic networking (services, ingress)

**Medium Overlap (40-60%)**:
- Autoscaling (Nephio adds intent-based scaling)
- Configuration management (Nephio adds packages)
- Observability (similar metrics, different aggregation)

**Low Overlap (10-30%)**:
- Multi-cluster orchestration (Nephio specializes here)
- Telco workloads (Nephio adds network functions)
- Automated lifecycle (Nephio's core strength)

## 🛡️ Mitigation Techniques by Attack Type

### 1. HTTP Flood (Application Layer)

**Kubernetes Native**:
- Resource quotas on pods
- HPA for auto-scaling
- NetworkPolicy to limit sources

**Istio**:
- Rate limiting (requests/second per IP)
- Circuit breakers on backend services
- Retry budgets to prevent cascade failures

**Nephio**:
- Intent-based scaling policies
- Automated package deployment for rate limiters
- Multi-cluster load distribution

### 2. SYN Flood (Network Layer)

**Kubernetes Native**:
- NetworkPolicy to filter TCP flags (limited)
- NodePort service limits
- iptables rules via DaemonSet

**Istio**:
- Connection limits per endpoint
- TCP keepalive tuning
- Envoy filter for SYN validation

**Nephio**:
- Network function chaining (firewall)
- Intent-based security policies
- Automated DPI deployment

### 3. Crossfire Attack (Infrastructure)

**Kubernetes Native**:
- Pod anti-affinity (distribute decoys)
- Resource quotas per namespace
- Priority classes for critical services
- NetworkPolicy egress controls

**Istio**:
- Per-service rate limiting
- Circuit breaking on decoy services
- Locality-aware load balancing
- Retry policies with jitter

**Nephio**:
- Multi-cluster deployment (geographic isolation)
- Intent-based traffic engineering
- Automated capacity injection
- Network topology awareness

### 4. Slowloris (Application Layer)

**Kubernetes Native**:
- Connection timeouts via annotations
- Resource limits (connections)
- Liveness/readiness probes

**Istio**:
- Request timeout policies
- Idle timeout configuration
- Connection pool limits
- HTTP/2 keep-alive tuning

**Nephio**:
- Workload API for timeout policies
- Automated configuration deployment
- Intent: "protect against slow attacks"

### 5. DNS Amplification (Network Layer)

**Kubernetes Native**:
- NetworkPolicy to restrict DNS ports
- CoreDNS rate limiting
- Resource quotas on DNS queries

**Istio**:
- ServiceEntry controls for external DNS
- Egress gateway filtering
- Rate limiting on DNS requests

**Nephio**:
- Intent-based DNS protection
- Automated DNS firewall deployment
- Multi-cluster DNS distribution

## 🔄 Deployment Workflow

### Phase 1: Baseline Protection (Native K8s)
```bash
# Apply foundational security
./scripts/deploy-native-baseline.sh
```

### Phase 2: Service Mesh Enhancement (Istio)
```bash
# Add intelligent traffic management
./scripts/deploy-istio-advanced.sh
```

### Phase 3: Automation Layer (Nephio)
```bash
# Enable intent-based automation
./scripts/deploy-nephio-orchestration.sh
```

## 📈 Testing Mitigations

### Test Against Crossfire Attack
```bash
# 1. Apply mitigations
kubectl apply -f kubernetes-native/
kubectl apply -f istio-advanced/

# 2. Run crossfire attack
python ../attack-simulations/crossfire-app-level.py \
    --target-url http://192.168.49.2:30001 \
    --decoy-services /catalogue,/cart,/tags \
    --workers 50 \
    --duration 300

# 3. Monitor effectiveness
kubectl top pods -n sock-shop
kubectl get hpa -n sock-shop
istioctl proxy-config clusters -n sock-shop <pod> | grep rate_limit
```

### Validation Metrics
- **Availability**: Target service uptime during attack
- **Latency**: P95 response time increase
- **Throughput**: Requests/second maintained
- **Resource Usage**: CPU/Memory compared to baseline
- **Detection Time**: Time to trigger autoscaling/rate limits

## 📚 Documentation

- **[Native Kubernetes Guide](kubernetes-native/README.md)**: All native K8s features
- **[Istio Advanced Guide](istio-advanced/README.md)**: Service mesh patterns
- **[Nephio Guide](nephio/README.md)**: Intent-based orchestration
- **[Comparison Analysis](comparative-analysis/README.md)**: Feature-by-feature comparison

## 🎓 Key Learnings

### When to Use Native Kubernetes
- Small to medium deployments
- Single cluster scenarios
- Team familiar with K8s primitives
- Need maximum control and transparency

### When to Add Istio
- Need advanced traffic management
- Require fine-grained rate limiting
- Want circuit breaking and retries
- Have complex service dependencies

### When to Use Nephio
- Multi-cluster/edge deployments
- Telco or network function workloads
- Need automated lifecycle management
- Want intent-based configuration
- Require package-based deployment

### Hybrid Approach (Recommended)
Use all three in layers:
1. **Foundation**: Native K8s for basic security and resources
2. **Intelligence**: Istio for traffic management and resilience
3. **Automation**: Nephio for multi-cluster orchestration

## 🚨 Production Recommendations

### Critical Protections (Must Have)
1. NetworkPolicy default deny
2. Resource quotas per namespace
3. PodDisruptionBudgets for critical services
4. HPA on frontend services
5. Istio rate limiting on public endpoints

### Enhanced Protections (Should Have)
1. Circuit breakers on backend services
2. Connection pool limits
3. Request timeouts
4. Retry budgets
5. Priority classes for workloads

### Advanced Protections (Nice to Have)
1. Multi-cluster failover (Nephio)
2. Geographic load distribution
3. Automated capacity injection
4. Intent-based policies
5. Self-healing automation

## 🔗 Related Resources

- [Crossfire Attack Simulations](../attack-simulations/)
- [ML Detection System](../ml-optimized-detector/)
- [Monitoring Setup](../monitoring/)
- [Istio Configuration](../istio/)

---

**Status**: Complete mitigation suite for Crossfire and other DDoS attacks
**Tested On**: Minikube with Sock Shop + Istio
**Last Updated**: November 2025
