# Attack Simulations

This directory contains advanced crossfire DDoS attack simulators for testing DDoS detection and mitigation systems.

## Overview

Crossfire attacks are **indirect DDoS attacks** that target decoy services (non-victim endpoints) to saturate shared network infrastructure and isolate the actual target. This framework provides:

- **Phase 4 Target Abstraction**: Generic attack adapter works with any web application, not just Sock Shop
- **Adaptive Attack Control**: Auto-adjusts rate based on target response
- **Traffic Shaping**: Multiple patterns (constant, burst, wave, random, ramp)
- **Multi-Vector Coordination**: Simultaneous app-level and network-level attacks
- **Stealth Capabilities**: Mimics legitimate traffic patterns
- **Configuration-Driven**: YAML-based attack strategies and target adapters

## Architecture

```
attacks/
├── target_adapter.py              # Phase 4: Generic target abstraction
├── crossfire_enhanced.py          # Enhanced app-level crossfire
├── network_crossfire_enhanced.py  # Enhanced network-level crossfire
├── orchestrator.py                # Multi-vector attack coordinator
├── endpoint-discovery.py          # Endpoint crawler for target reconnaissance
├── configs/
│   ├── attack-strategies/         # Pre-configured attack strategies
│   │   ├── stealth-test.yaml
│   │   ├── balanced-test.yaml
│   │   └── aggressive-stress.yaml
│   └── target-adapters/           # Target-specific configurations
│       └── sock-shop.yaml
└── [legacy scripts]               # Original attack scripts
```

---

## Quick Start

### 1. Discover Target Endpoints

```bash
# Crawl target application to discover endpoints
python3 endpoint-discovery.py --url http://target:8080 --max-depth 3 --output discovered-endpoints.json
```

### 2. Run Enhanced App-Level Attack

```bash
# Moderate attack with adaptive control
python3 crossfire_enhanced.py \
  --url http://target:8080 \
  --discovery-file discovered-endpoints.json \
  --mode moderate \
  --pattern burst \
  --duration 120 \
  --workers 50
```

### 3. Run Enhanced Network-Level Attack (requires root)

```bash
# Multi-protocol network flooding
sudo python3 network_crossfire_enhanced.py \
  --url http://target:8080 \
  --discovery-file discovered-endpoints.json \
  --protocol mixed \
  --pattern wave \
  --duration 120 \
  --workers 10 \
  --pps 5000
```

### 4. Run Coordinated Multi-Vector Attack

```bash
# Orchestrated attack with phased execution
python3 orchestrator.py \
  --strategy aggressive \
  --url http://target:8080 \
  --adapter-config configs/target-adapters/sock-shop.yaml \
  --discovery-file discovered-endpoints.json \
  --duration 300
```

---

## Enhanced Attack Features

### Phase 4 Target Adapter

The `target_adapter.py` provides a **generic abstraction layer** that allows attacks to work with any web application:

**Key Features**:
- Load target configurations from YAML
- Integrate endpoint discovery results
- Weighted endpoint selection for crossfire
- Attack profile management (stealth, moderate, aggressive, extreme)
- Intelligent crossfire strategy suggestions
- Export monitoring data for detection systems

**Usage in Attack Scripts**:
```python
from target_adapter import create_adapter

# Create adapter with discovery integration
adapter = create_adapter(
    base_url="http://target:8080",
    adapter_config="configs/target-adapters/sock-shop.yaml",
    discovery_file="discovered-endpoints.json"
)

# Get decoy endpoints (never includes target endpoints)
decoys = adapter.get_decoy_endpoints(limit=10)

# Get weighted endpoint (higher weight = higher selection probability)
endpoint = adapter.get_weighted_endpoint()

# Get attack profile
profile = adapter.get_attack_profile("aggressive")
# profile.requests_per_second, profile.burst_size, etc.

# Get crossfire strategy recommendation
strategy = adapter.suggest_crossfire_strategy()
```

### Enhanced Application-Level Crossfire

**File**: [crossfire_enhanced.py](crossfire_enhanced.py)

**Improvements over legacy version**:
1. **Adaptive Rate Control**: Auto-adjusts request rate based on target response (success rate)
2. **Traffic Patterns**: 5 patterns (constant, burst, wave, random, ramp)
3. **Stealth Mode**: User-agent rotation, realistic headers, randomized timing
4. **Target Adapter Integration**: Works with any target application
5. **Real-Time Metrics**: Success/fail rates, latency, error breakdowns
6. **Graceful Degradation**: Circuit breaker on errors

**Attack Modes**:
- `stealth`: 5 req/s per worker, mimics normal traffic
- `moderate`: 50 req/s per worker, balanced
- `aggressive`: 200 req/s per worker, obvious attack
- `extreme`: 1000 req/s per worker, overwhelming
- `adaptive`: Auto-adjusts based on target response

**Traffic Patterns**:
- `constant`: Steady rate
- `burst`: Periodic bursts (5x rate every 5s)
- `wave`: Sine wave pattern (0.5x to 1.5x rate)
- `random`: Random jitter (0.5x to 1.5x rate)
- `ramp`: Gradually increase from 0.5x to 2x

**Usage Examples**:

```bash
# Stealth attack with randomization
python3 crossfire_enhanced.py \
  --url http://target:8080 \
  --adapter-config configs/target-adapters/sock-shop.yaml \
  --mode stealth \
  --pattern random \
  --stealth \
  --duration 300 \
  --workers 10

# Adaptive burst attack
python3 crossfire_enhanced.py \
  --url http://target:8080 \
  --discovery-file discovered-endpoints.json \
  --mode adaptive \
  --pattern burst \
  --duration 180 \
  --workers 50 \
  --rate 100

# Extreme stress test with wave pattern
python3 crossfire_enhanced.py \
  --url http://target:8080 \
  --adapter-config configs/target-adapters/sock-shop.yaml \
  --mode extreme \
  --pattern wave \
  --duration 600 \
  --workers 200
```

### Enhanced Network-Level Crossfire

**File**: [network_crossfire_enhanced.py](network_crossfire_enhanced.py)

**Improvements over legacy version**:
1. **Multiple Protocols**: SYN, ACK, RST, UDP, MIXED
2. **Adaptive Packet Rate**: Auto-adjusts based on network response
3. **Traffic Shaping**: Burst, wave, constant, random patterns
4. **Intelligent Source IPs**: Avoids reserved ranges
5. **Target Adapter Integration**: Extracts IPs from discovered endpoints
6. **Per-Target Metrics**: Track packets per IP and protocol

**Protocols**:
- `syn`: TCP SYN flood (classic)
- `ack`: TCP ACK flood
- `rst`: TCP RST flood
- `udp`: UDP flood
- `mixed`: Randomly mix all protocols

**Usage Examples**:

```bash
# Multi-protocol burst flood
sudo python3 network_crossfire_enhanced.py \
  --url http://target:8080 \
  --discovery-file discovered-endpoints.json \
  --protocol mixed \
  --pattern burst \
  --duration 120 \
  --workers 20 \
  --pps 5000

# Adaptive SYN flood
sudo python3 network_crossfire_enhanced.py \
  --url http://target:8080 \
  --adapter-config configs/target-adapters/sock-shop.yaml \
  --protocol syn \
  --pattern constant \
  --duration 300 \
  --workers 10 \
  --pps 10000

# UDP flood with wave pattern
sudo python3 network_crossfire_enhanced.py \
  --url http://target:8080 \
  --discovery-file discovered-endpoints.json \
  --protocol udp \
  --pattern wave \
  --duration 180 \
  --workers 15 \
  --pps 8000
```

**Requirements**:
- Root privileges or `CAP_NET_RAW` capability
- Run with `sudo` or grant capability: `sudo setcap cap_net_raw+ep /usr/bin/python3`

### Attack Orchestrator

**File**: [orchestrator.py](orchestrator.py)

**Features**:
- **Multi-Vector Coordination**: Launch app-level and network-level attacks simultaneously
- **Phased Execution**: Ramp-up → Sustain → Ramp-down
- **Strategy Management**: Load attack strategies from YAML configs
- **Built-in Strategies**: stealth, moderate, aggressive, extreme
- **Centralized Telemetry**: Collect metrics from all attack vectors
- **Graceful Shutdown**: Terminate all processes cleanly

**Attack Phases**:
1. **Ramp-Up**: Gradually increase attack intensity (default: 30s)
2. **Sustain**: Maintain peak intensity (calculated from total duration)
3. **Ramp-Down**: Gradually decrease intensity (default: 30s)

**Usage with Built-in Strategies**:

```bash
# Stealth test (app-level only, low intensity)
python3 orchestrator.py \
  --strategy stealth \
  --url http://target:8080 \
  --duration 180

# Moderate balanced attack (app + network)
python3 orchestrator.py \
  --strategy moderate \
  --url http://target:8080 \
  --adapter-config configs/target-adapters/sock-shop.yaml \
  --duration 300

# Aggressive stress test
python3 orchestrator.py \
  --strategy aggressive \
  --url http://target:8080 \
  --discovery-file discovered-endpoints.json \
  --duration 600

# Extreme capacity test
python3 orchestrator.py \
  --strategy extreme \
  --url http://target:8080 \
  --adapter-config configs/target-adapters/sock-shop.yaml \
  --duration 900 \
  --verbose
```

**Usage with Custom Strategy YAML**:

```bash
python3 orchestrator.py \
  --config configs/attack-strategies/balanced-test.yaml \
  --duration 300
```

---

## Configuration System

### Target Adapter Configuration

**File**: `configs/target-adapters/sock-shop.yaml`

Define target-specific endpoints, weights, and service topology:

```yaml
target_name: "sock-shop"
target_service: "front-end"
base_url: "http://front-end:8080"

endpoints:
  # Target endpoint (protected, never attacked)
  - path: "/catalogue"
    method: "GET"
    weight: 0.0  # NEVER attack directly
    resource_cost: 5
    category: "target"
  
  # Decoy endpoints (flood these)
  - path: "/login"
    method: "GET"
    weight: 2.0  # 2x selection probability
    resource_cost: 3
    category: "decoy"
  
  # ... more endpoints ...

services:
  - name: "front-end"
    ip: "10.0.0.10"
    ports: [8080]
  
  # ... more services ...

recommendations:
  recommended_profile: "moderate"
  stealth_profile: "stealth"
```

**Endpoint Weights**:
- `0.0`: Never select (target endpoints)
- `1.0`: Normal selection probability
- `2.0+`: Higher selection probability (prioritize high-cost endpoints)

### Attack Strategy Configuration

**File**: `configs/attack-strategies/balanced-test.yaml`

Define multi-vector attack strategies:

```yaml
name: "Balanced Test"
description: "Moderate multi-vector attack for testing mitigations"

target_url: "http://target-service:8080"
adapter_config: "configs/target-adapters/sock-shop.yaml"
discovery_file: "discovered-endpoints.json"

app_level:
  enabled: true
  script: "crossfire_enhanced.py"
  workers: 50
  mode: "moderate"
  pattern: "constant"
  rate: 50
  additional_args:
    no-adaptation: false

network_level:
  enabled: true
  script: "network_crossfire_enhanced.py"
  workers: 10
  protocol: "syn"
  pattern: "constant"
  pps: 1000

phases:
  ramp_up: 30
  ramp_down: 30
```

---

## Legacy Attack Scripts

### Application-Level Crossfire (Legacy)

**File**: [crossfire-app-level.py](crossfire-app-level.py)

Original implementation without adaptive control or traffic shaping.

**Usage**:
```bash
python3 crossfire-app-level.py --url http://localhost:8080 --duration 60 --rate 10 --workers 10
```

### Network-Level Crossfire (Legacy)

**File**: [crossfire-network-level.py](crossfire-network-level.py)

Original implementation with basic SYN flooding.

**Usage** (requires root):
```bash
sudo python3 crossfire-network-level.py --duration 60 --rate 100 --threads 5

**Total Packet Rate**: `rate × threads` packets/second

## Prerequisites

### For Application-Level Attacks

```bash
pip3 install -r requirements.txt
```

Required packages:
- `aiohttp` - Async HTTP client
- `asyncio` - Async I/O

### For Network-Level Attacks

- Root privileges (CAP_NET_RAW)
- Python 3.7+
- No additional packages required (uses standard library)

## Quick Start

### Orchestrated Simulation

Use the orchestrator script for a complete simulation with monitoring:

```bash
# Application-level attack
./scripts/run-attack-simulation.sh app 60 10 10

# Network-level attack
./scripts/run-attack-simulation.sh network 60 5 100
```

The orchestrator will:
1. Verify prerequisites
2. Start monitoring tools
3. Collect baseline metrics
4. Execute the attack
5. Observe recovery
6. Clean up

### Manual Execution

#### Step 1: Prepare Monitoring

```bash
# Open monitoring dashboards
./scripts/access-monitoring.sh
```

Open in your browser:
- Kiali: http://localhost:20001
- Grafana: http://localhost:3000
- Prometheus: http://localhost:9090

#### Step 2: Collect Baseline

Observe normal traffic for 30-60 seconds to establish baseline metrics.

#### Step 3: Run Attack

```bash
# Application-level
python3 attack-simulations/crossfire-app-level.py --duration 60 --rate 10 --workers 10

# Network-level (requires root)
sudo python3 attack-simulations/crossfire-network-level.py --duration 60 --rate 100 --threads 5
```

#### Step 4: Observe

Watch the monitoring dashboards during the attack:
- **Kiali**: Traffic flow and service topology
- **Grafana**: Request rates, latency, errors
- **Prometheus**: Raw metrics and alerts

#### Step 5: Post-Attack Analysis

After the attack ends, observe:
- Recovery time
- Lingering effects
- Circuit breaker activations
- Error rates

## Attack Strategies

### Strategy 1: Gradual Ramp-Up

Slowly increase attack intensity:

```bash
# Start with low rate
python3 crossfire-app-level.py --duration 30 --rate 5 --workers 5

# Increase rate
python3 crossfire-app-level.py --duration 30 --rate 10 --workers 10

# High intensity
python3 crossfire-app-level.py --duration 30 --rate 20 --workers 20
```

### Strategy 2: Burst Attack

Short, high-intensity bursts:

```bash
# 15-second bursts with high rate
python3 crossfire-app-level.py --duration 15 --rate 50 --workers 20
```

### Strategy 3: Sustained Attack

Long-duration, moderate intensity:

```bash
# 5-minute sustained attack
python3 crossfire-app-level.py --duration 300 --rate 15 --workers 10
```

### Strategy 4: Multi-Vector

Combine application and network attacks:

```bash
# Terminal 1: Application-level
python3 crossfire-app-level.py --duration 60 --rate 10 --workers 10

# Terminal 2: Network-level (simultaneously)
sudo python3 crossfire-network-level.py --duration 60 --rate 100 --threads 5
```

## Targeted Services

### Decoy Services (Flooded)

The application-level attack targets these endpoints:
- `/catalogue` - Product catalog
- `/catalogue/size` - Catalog size
- `/tags` - Product tags
- `/cart` - Shopping cart
- `/cards` - Payment cards
- `/addresses` - User addresses

### Victim Service (Indirectly Affected)

- `front-end` - The main user interface

The goal is to degrade the front-end service by overwhelming decoy services that share infrastructure (network, CPU, memory).

## Metrics to Monitor

### Application Metrics

- **Request Rate**: Sudden spike in decoy services
- **Error Rate**: Increase in 5xx errors
- **Latency**: P95/P99 response time degradation
- **Success Rate**: Decrease in successful requests

### Infrastructure Metrics

- **CPU Usage**: Increase across pods
- **Memory Usage**: Potential OOM conditions
- **Network Bytes**: Bandwidth saturation
- **Active Connections**: Connection pool exhaustion

### Istio Metrics

- **Circuit Breaker**: Activations and overflows
- **Connection Pool**: Utilization and pending requests
- **Retry Budget**: Exhaustion
- **Mesh Traffic**: Inter-service communication

## Expected Outcomes

### Successful Attack Indicators

1. **Decoy Service Overload**:
   - High request rate on targeted services
   - Increased latency
   - Error rate spike

2. **Network Saturation**:
   - High bandwidth utilization
   - Packet loss
   - Connection timeouts

3. **Target Service Degradation**:
   - Slower response times on front-end
   - Reduced availability
   - User experience degradation

4. **Circuit Breaker Activation**:
   - Connection overflow events
   - Service isolation
   - Cascading failures

### Defense Mechanisms

Observe these Istio protections:
- **Connection Pooling**: Limits concurrent connections
- **Circuit Breaking**: Isolates failing services
- **Retry Budgets**: Prevents retry storms
- **Rate Limiting**: (if configured) Throttles requests

## Troubleshooting

### Application Attack Not Working

**Issue**: No impact on services

**Solutions**:
1. Verify app URL is correct:
   ```bash
   curl http://localhost:8080
   ```
2. Check if port-forward is active:
   ```bash
   kubectl port-forward -n sock-shop svc/front-end 8080:80
   ```
3. Increase attack intensity:
   ```bash
   python3 crossfire-app-level.py --rate 50 --workers 20
   ```

### Network Attack Fails

**Issue**: Permission denied

**Solution**: Run with root privileges:
```bash
sudo python3 crossfire-network-level.py
```

**Issue**: No target IPs found

**Solution**: Verify sock-shop is deployed:
```bash
kubectl get pods -n sock-shop -o wide
```

### No Metrics in Monitoring

**Issue**: Dashboards empty

**Solutions**:
1. Generate normal traffic first:
   ```bash
   while true; do curl http://localhost:8080; sleep 1; done
   ```
2. Verify Istio sidecar injection:
   ```bash
   kubectl get pods -n sock-shop
   # Should show 2/2 containers
   ```
3. Restart pods to inject sidecars:
   ```bash
   kubectl rollout restart deployment -n sock-shop
   ```

## Safety and Ethics

⚠️ **IMPORTANT**: These tools are for educational purposes only.

- Only use in controlled environments
- Never attack systems you don't own
- Obtain explicit permission before testing
- Be aware of resource consumption
- Monitor system health during tests
- Have a kill switch ready (`Ctrl+C`)

---

## Endpoint Discovery

**File**: [endpoint-discovery.py](endpoint-discovery.py)

Crawls target application to discover HTTP endpoints for crossfire targeting.

**Usage**:
```bash
python3 endpoint-discovery.py --url http://target:8080 --max-depth 3 --output discovered-endpoints.json
```

---

## Best Practices

### Scaling Attacks

1. **Worker Count**: Increase `--workers` for higher concurrency
   - App-level: 10-200 workers depending on target capacity
   - Network-level: 5-50 workers (each spawns raw sockets)

2. **Request/Packet Rate**: Increase `--rate` or `--pps`
   - Start conservative (rate=10, pps=1000)
   - Monitor target response and scale up

3. **Traffic Patterns**:
   - `constant`: Maximum sustained pressure
   - `burst`: Periodic spikes to test burst handling
   - `wave`: Gradual oscillation for stress testing
   - `ramp`: Gradual increase to find breaking points

4. **Adaptive Mode**: Let attacks auto-scale based on target response
   ```bash
   --mode adaptive  # Auto-adjusts rate
   ```

### Reliability Configuration

1. **Stealth Mode**: Avoid detection systems
   ```bash
   --mode stealth --pattern random --stealth
   ```

2. **Multi-Protocol**: Increase reliability by mixing protocols
   ```bash
   --protocol mixed  # Network-level
   ```

3. **Phased Execution**: Use orchestrator for realistic attack patterns
   ```bash
   python3 orchestrator.py --strategy moderate --duration 300
   ```

4. **Error Handling**: Enable graceful degradation
   ```bash
   # Adaptive mode includes circuit breaker
   --mode adaptive
   ```

### Target Adapter Benefits

1. **Generic Attack**: Works with ANY web application
2. **Weighted Selection**: Prioritize high-cost endpoints
3. **Intelligent Strategy**: Get recommendations based on target topology
4. **Discovery Integration**: Automatically use crawled endpoints

---

## Troubleshooting

### Permission Denied (Network Attacks)

**Problem**: `PermissionError: [Errno 1] Operation not permitted`

**Solution**: Network attacks require root or `CAP_NET_RAW`:
```bash
# Option 1: Run with sudo
sudo python3 network_crossfire_enhanced.py ...

# Option 2: Grant capability
sudo setcap cap_net_raw+ep $(which python3)
```

### Import Error: target_adapter

**Problem**: `ModuleNotFoundError: No module named 'target_adapter'`

**Solution**: Run scripts from `attacks/` directory:
```bash
cd attacks/
python3 crossfire_enhanced.py ...
```

### No Endpoints Discovered

**Problem**: `ERROR: No decoy endpoints found`

**Solution**: Either provide adapter config or discovery file:
```bash
# Option 1: Use adapter config
--adapter-config configs/target-adapters/sock-shop.yaml

# Option 2: Run discovery first
python3 endpoint-discovery.py --url http://target:8080 --output discovered-endpoints.json
--discovery-file discovered-endpoints.json
```

### Connection Refused

**Problem**: Target not reachable

**Solution**: Verify target is running and accessible:
```bash
curl http://target:8080  # Should return 200
kubectl get svc  # Check Kubernetes service
```

---

## Performance Benchmarks

### Application-Level Attack

| Mode | Workers | Rate (req/s/worker) | Total Rate | CPU Usage | Memory |
|------|---------|---------------------|------------|-----------|--------|
| Stealth | 10 | 5 | 50 req/s | 5-10% | ~50MB |
| Moderate | 50 | 50 | 2,500 req/s | 20-30% | ~200MB |
| Aggressive | 100 | 200 | 20,000 req/s | 50-70% | ~500MB |
| Extreme | 200 | 1000 | 200,000 req/s | 90-100% | ~1GB |

### Network-Level Attack (requires root)

| Protocol | Workers | PPS | Total Rate | CPU Usage | Memory |
|----------|---------|-----|------------|-----------|--------|
| SYN | 10 | 1000 | 10,000 pkt/s | 10-20% | ~30MB |
| MIXED | 20 | 5000 | 100,000 pkt/s | 40-60% | ~60MB |
| UDP | 50 | 10000 | 500,000 pkt/s | 80-100% | ~100MB |

### Multi-Vector (Orchestrated)

| Strategy | Total Rate | Duration | CPU Usage | Memory |
|----------|------------|----------|-----------|--------|
| Stealth | 50 req/s + 0 pkt/s | 180s | 10% | ~50MB |
| Moderate | 2.5K req/s + 10K pkt/s | 300s | 40% | ~250MB |
| Aggressive | 20K req/s + 100K pkt/s | 600s | 80% | ~600MB |
| Extreme | 200K req/s + 500K pkt/s | 900s | 100% | ~1.2GB |

---

## Safety and Ethics

⚠️ **WARNING**: These tools generate real DDoS attacks. Use responsibly:

1. **Only test YOUR OWN infrastructure** or environments you have explicit permission to test
2. **Never target production systems** without proper authorization and change control
3. **Use isolated test environments** (Kubernetes clusters, VMs, etc.)
4. **Monitor target systems** to prevent actual damage or service disruption
5. **Have mitigation ready** before running attacks
6. **Document all testing** for compliance and audit purposes

**Legal Notice**: Unauthorized DDoS attacks are illegal in most jurisdictions. Always obtain written permission before testing.

---

## Integration with Detection and Mitigation

### With ML Detector

```bash
# 1. Start continuous monitoring
cd ../ml-detector
python3 continuous_monitor.py --duration 600 &

# 2. Run attack
cd ../attacks
python3 orchestrator.py --strategy moderate --url http://target:8080 --duration 300

# 3. Check detection results
cd ../ml-detector
python3 cli_monitor.py --check-status
```

### With Kubernetes Mitigations

```bash
# 1. Deploy mitigations
cd ../mitigations/kubernetes-native
./deploy.sh

# 2. Run attack to test
cd ../../attacks
python3 crossfire_enhanced.py --url http://target:8080 --mode aggressive --duration 120

# 3. Verify mitigation effectiveness
kubectl top pods
kubectl get hpa
```

### With Istio Advanced

```bash
# 1. Deploy Istio mitigations
cd ../mitigations/istio-advanced
kubectl apply -f rate-limiting.yaml
kubectl apply -f circuit-breaker.yaml

# 2. Test with graduated attack
cd ../../attacks
python3 orchestrator.py --strategy aggressive --url http://target:8080 --duration 300

# 3. Monitor Istio metrics
istioctl dashboard kiali
```

---

## See Also

- **[ML Detector Documentation](../ml-detector/README.md)**: Machine learning-based crossfire detection
- **[Mitigation Techniques](../mitigations/README.md)**: Kubernetes, Istio, and Nephio mitigations
- **[Test Scenarios](../TEST_SCENARIOS.md)**: End-to-end testing workflows
- **[Architecture Documentation](../docs/architecture/)**: System design and attack patterns

---

## Contributing

When adding new attack scripts:

1. **Follow Phase 4 pattern**: Use `target_adapter.py` for target abstraction
2. **Add metrics collection**: Track success/fail rates, latency, errors
3. **Support configuration**: Accept YAML configs and discovery files
4. **Document usage**: Add examples to this README
5. **Add orchestrator support**: Make script work with `orchestrator.py`
6. **Test thoroughly**: Verify against multiple target applications

---

## Advanced Usage

### Custom Attack Patterns

Modify the scripts to:
- Target specific endpoints
- Implement sophisticated traffic patterns
- Simulate real-world attack scenarios
- Test custom defense mechanisms

### Integration with CI/CD

Use for chaos engineering:
```bash
# Run attack as part of resilience testing
./scripts/run-attack-simulation.sh app 30 5 5
# Verify service recovery
# Assert SLA compliance
```

### Data Export

Export attack results for analysis:
```python
# Modify attack scripts to export to JSON/CSV
import json
with open('attack-results.json', 'w') as f:
    json.dump(stats, f)
```

## Additional Resources

- See [Attack Simulations Guide](../docs/05-attack-simulations.md) for detailed documentation
- See [Monitoring Setup](../docs/04-monitoring-setup.md) for observability guidance
- See [Crossfire Attack Paper](https://doi.org/10.1145/2398776.2398797) for theoretical background

---

## License

This project is for **educational and research purposes only**. See [../LICENSE](../LICENSE) for details.
