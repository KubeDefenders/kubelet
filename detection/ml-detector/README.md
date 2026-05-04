# ML-Optimized DDoS Detector

> **Standalone Project:** This detector is fully self-contained and does not require any other components from the parent repository. All dependencies and dataset requirements are documented below.

## Overview

This is an optimized ML-based DDoS detection system that uses **anomaly detection** trained on CICDDoS2019 dataset and detects attacks from Istio service mesh metrics, with explainable AI (xAI) for attack analysis.

## Quick Start

```bash
# 1. Setup dataset (interactive helper)
./scripts/training/setup_dataset.sh

# 2. Install dependencies
pip install -r requirements.txt

# 3. Train models
python train_detector.py --dataset data/cicddos2019 --output models/

# 4. Run detection
python cli_monitor.py --prometheus http://localhost:9090 --model models/ensemble.pkl
```

**Documentation:**
- **Quick Start:** [docs/guides/QUICKSTART.md](docs/guides/QUICKSTART.md)
- **Architecture:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- **Project Structure:** [docs/PROJECT_STRUCTURE.md](docs/PROJECT_STRUCTURE.md)

## Key Design Decisions

### 1. Anomaly Detection (Not Classification)
- **One-Class SVM** + **Isolation Forest** ensemble
- Trained ONLY on normal CICDDoS2019 traffic to learn baseline behavior
- Detects deviations from normal patterns (any attack type)
- Handles dataset domain mismatch better than supervised classification

### 2. Statistical Feature Mapping
- Focus on **distribution-invariant features** that transfer well:
  - Rate statistics (mean, std, variance)
  - Inter-arrival time patterns
  - Packet size distributions
  - Flow duration characteristics
- Avoid absolute value features that don't transfer (IP addresses, ports, etc.)

### 3. Explainable AI (xAI)
- **SHAP (TreeExplainer)** for Isolation Forest
- **Kernel SHAP** for One-Class SVM
- Feature contribution analysis shows WHY traffic is anomalous

### 4. No Synthetic Data
- Uses only real CICDDoS2019 normal traffic
- Maps Istio metrics to equivalent statistical features
- Accepts domain shift as limitation (detection > perfect accuracy)

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Training Phase                          │
│  CICDDoS2019 (Normal Only) → Feature Engineering →          │
│  One-Class SVM + Isolation Forest Training                  │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│                    Detection Phase                          │
│  Istio Metrics → Statistical Features → Ensemble →          │
│  Anomaly Score → SHAP Explanation → Alert                   │
└─────────────────────────────────────────────────────────────┘
```

## Components

1. **detector_engine.py** (in `src/`) - Real-time detection with xAI explanations
2. **feature_extractor.py** (in `src/`) - Extract statistical features from Istio metrics
3. **train_detector.py** - Train anomaly detection models on CICDDoS2019
4. **cli_monitor.py** - Command-line monitoring interface
5. **config.yaml** - Configuration parameters

See [docs/PROJECT_STRUCTURE.md](docs/PROJECT_STRUCTURE.md) for complete file organization.

## Features Extracted (35 core features)

### Rate Features (10)
- Request rate (mean, std, percentiles)
- Packet rate equivalents
- Byte rate statistics

### Latency Features (8)
- P50, P95, P99 latencies
- Latency variance and trends
- Inter-arrival time statistics

### Size Features (7)
- Request/response size distributions
- Size variance and ratios

### Error Features (5)
- Error rate and patterns
- 4xx/5xx distributions

### Flow Features (5)
- Flow duration estimates
- Connection patterns
- Traffic burstiness

## Usage

### 1. Obtain CICDDoS2019 Dataset

Download the CICDDoS2019 dataset and place it in a location of your choice:
- **Official source:** [CICDDoS2019 Dataset](https://www.unb.ca/cic/datasets/ddos-2019.html)
- **Detailed guide:** See [docs/guides/DATASET_SETUP.md](docs/guides/DATASET_SETUP.md) for complete instructions
- **Recommended location:** `data/cicddos2019/`

Quick setup:
```bash
./scripts/training/setup_dataset.sh
# Or manually:
mkdir -p data/cicddos2019
# Download and extract dataset to data/cicddos2019/
```

### 2. Train Models
```bash
# If using recommended location
python train_detector.py --dataset data/cicddos2019 --output models/

# Or specify your custom path
python train_detector.py --dataset /path/to/cicddos2019 --output models/
```

### 3. Run Detection
```bash
python cli_monitor.py --prometheus http://localhost:9090 --model models/ensemble.pkl
```

### 4. View Explanations
Explanations are automatically generated for each detected anomaly showing:
- Anomaly score
- Top contributing features
- SHAP values
- Recommended actions

## Limitations & Tradeoffs

✅ **Strengths:**
- Detects any attack pattern (not limited to training labels)
- Handles domain shift better than classification
- Requires only normal training data
- Explainable predictions

⚠️ **Limitations:**
- May have false positives on legitimate traffic spikes
- Detection threshold needs tuning for environment
- Feature mapping is approximate (network → application level)
- Cannot identify specific attack types precisely

## Performance Expectations

- **Detection Rate:** 70-85% (accepts domain shift)
- **False Positive Rate:** 5-15% (tunable via threshold)
- **Latency:** <100ms per prediction
- **Explanation Generation:** <200ms

## Configuration

Key parameters in `config.yaml`:
- `anomaly_threshold`: Sensitivity (lower = more sensitive)
- `ensemble_weights`: Balance between models
- `feature_windows`: Time windows for aggregation
- `shap_samples`: Background samples for SHAP
