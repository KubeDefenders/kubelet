"""
detection_v2 — Decoupled, reliable DDoS anomaly detection.

Package layout:
  detection_v2/
    core/         — Zero-dependency detection logic (schema, features, model, explainer)
    adapters/     — External data source adapters (Prometheus / Istio, dataset loaders)
    training/     — Model training pipeline
    monitor/      — Runtime monitoring loop and CLI
"""
