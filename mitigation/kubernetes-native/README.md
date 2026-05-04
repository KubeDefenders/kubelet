# Kubernetes Native DDoS Mitigations

Native Kubernetes features for DDoS protection without external dependencies.

## 🎯 Mitigation Strategies

### 1. Network Isolation (NetworkPolicies)
Prevent Crossfire attacks by isolating traffic flows and limiting blast radius.

### 2. Resource Protection (Quotas & Limits)
Prevent resource exhaustion through controlled allocation.

### 3. Availability (Autoscaling & PDB)
Maintain service availability under load through scaling and disruption control.

### 4. Traffic Control (Service & Ingress)
Shape and limit incoming traffic at the edge.

## 📊 Coverage Matrix

| Attack Type | Mitigation | Effectiveness | Limitations |
|-------------|-----------|---------------|-------------|
| HTTP Flood | HPA + Quotas | 70% | Can't block malicious IPs |
| SYN Flood | NetworkPolicy | 40% | Limited TCP flag filtering |
| Slowloris | Timeouts + Limits | 60% | App-level control needed |
| DNS Amp | NetworkPolicy + CoreDNS | 80% | Needs proper DNS config |
| Crossfire App | Anti-affinity + Quotas | 75% | Can't prevent link saturation |
| Crossfire Net | NetworkPolicy | 50% | Limited network-layer control |

## 🚀 Quick Deploy

```bash
# Apply all native mitigations
kubectl apply -f network-policies/
kubectl apply -f resource-quotas/
kubectl apply -f autoscaling/
kubectl apply -f pod-disruption/
```

## 📁 Contents

- **network-policies/**: NetworkPolicy configurations for traffic isolation
- **resource-quotas/**: ResourceQuota and LimitRange definitions
- **autoscaling/**: HPA and VPA configurations
- **pod-disruption/**: PodDisruptionBudget for availability

## ✅ Deployment Checklist

- [ ] NetworkPolicies applied (default deny + allowlist)
- [ ] ResourceQuotas set per namespace
- [ ] LimitRanges configured for pods
- [ ] HPA configured for frontend services
- [ ] PDB set for critical services
- [ ] Priority classes defined
- [ ] Node affinity rules set
- [ ] Taints and tolerations configured

## 🔍 Verification

```bash
# Check NetworkPolicies
kubectl get networkpolicies -n sock-shop

# Check ResourceQuotas
kubectl get resourcequota -n sock-shop

# Check HPA status
kubectl get hpa -n sock-shop

# Check PDB
kubectl get pdb -n sock-shop
```
