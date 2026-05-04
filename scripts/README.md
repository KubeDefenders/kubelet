# Script Catalog

Scripts are now organized by workflow so you can find provisioning, attack, ML, and operational helpers quickly. All entry points remain executable directly from this folder structure; run them with `bash path/to/script.sh` or make them executable and invoke directly.

| Category | Location | Purpose |
|----------|----------|---------|
| Cluster setup | `scripts/cluster/` | Spin up Minikube, install Istio, deploy Sock Shop, reinstall components |
| Attack & load | `scripts/attacks/` | Launch scenario-based attacks, traffic generators, and archived older flows |
| ML & detection | `scripts/ml/` | Collect datasets, train models, start detectors, run monitoring/test harnesses |
| Ops & observability | `scripts/ops/` | Utility helpers such as accessing monitoring dashboards |

## Script reference

### Cluster (`scripts/cluster/`)
- `setup-minikube.sh`
- `reinstantiate-minikube.sh`
- `setup-istio.sh`
- `setup-monitoring.sh`
- `deploy-sock-shop.sh`

### Attacks (`scripts/attacks/`)
- `run-attack-simulation.sh`
- `run-scenario-app-attack.sh`
- `run-scenario-network-attack.sh`
- `run-scenario-complete.sh`
- `run-scenario-discovery.sh`
- `run-locust.sh`
- `archive/run-attack-simulation.sh.old`

### ML (`scripts/ml/`)
- `collect-cicddos-real-features.sh`
- `collect-real-training-data.sh`
- `train-cicddos-model.sh`
- `train-ml-model.sh`
- `train-prometheus-model.sh`
- `start-cicddos-detector.sh`
- `start-ml-detector.sh`
- `test-attack-detection.sh`
- `test.sh`
- `monitor.sh`
- `start.sh`

### Ops (`scripts/ops/`)
- `access-monitoring.sh`

> Module-specific scripts (e.g., anything inside `ml-detection/` or `attack-simulations/`) remain in their respective directories because they are tightly coupled to that code and rely on relative paths within those modules.
