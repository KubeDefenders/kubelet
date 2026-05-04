"""
KubeDDoS System - Runtime configuration.

All values come from environment variables or sensible defaults.
No hardcoded cluster IPs, namespaces, or architecture assumptions.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SystemConfig:
    """Top-level configuration for the KubeDDoS system frontend."""

    # Kubernetes API
    k8s_api_url: str = field(
        default_factory=lambda: os.getenv(
            "KUBERNETES_API_URL", "https://kubernetes.default.svc"
        )
    )
    k8s_token_path: str = field(
        default_factory=lambda: os.getenv(
            "K8S_TOKEN_PATH", "/var/run/secrets/kubernetes.io/serviceaccount/token"
        )
    )
    k8s_ca_path: str = field(
        default_factory=lambda: os.getenv(
            "K8S_CA_PATH", "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
        )
    )

    # Prometheus
    prometheus_url: str = field(
        default_factory=lambda: os.getenv(
            "PROMETHEUS_URL", "http://kube-prometheus-stack-prometheus.monitoring.svc.cluster.local:9090"
        )
    )

    # Crossfire CRD
    crd_group: str = "crossfire.io"
    crd_version: str = "v1alpha1"
    event_plural: str = "crossfiredetectionevents"
    intent_plural: str = "crossfiremitigationintents"

    # Target namespace to monitor
    target_namespace: str = field(
        default_factory=lambda: os.getenv("TARGET_NAMESPACE", "sock-shop")
    )

    # Detection thresholds (read from env for ConfigMap injection)
    link_utilization_threshold_pct: float = field(
        default_factory=lambda: float(os.getenv("LINK_UTILIZATION_THRESHOLD_PCT", "80.0"))
    )
    decoy_fraction_threshold: float = field(
        default_factory=lambda: float(os.getenv("DECOY_FRACTION_THRESHOLD", "0.5"))
    )
    victim_error_rate_threshold_pct: float = field(
        default_factory=lambda: float(os.getenv("VICTIM_ERROR_RATE_THRESHOLD_PCT", "5.0"))
    )
    victim_direct_traffic_max_pct: float = field(
        default_factory=lambda: float(os.getenv("VICTIM_DIRECT_TRAFFIC_MAX_PCT", "10.0"))
    )
    confidence_threshold: float = field(
        default_factory=lambda: float(os.getenv("CONFIDENCE_THRESHOLD", "0.70"))
    )

    # Model
    model_path: str = field(
        default_factory=lambda: os.getenv("MODEL_PATH", "models/detector_v2.pkl")
    )

    # Frontend
    frontend_port: int = field(
        default_factory=lambda: int(os.getenv("FRONTEND_PORT", "5000"))
    )
    frontend_host: str = field(
        default_factory=lambda: os.getenv("FRONTEND_HOST", "0.0.0.0")
    )
    secret_key: str = field(
        default_factory=lambda: os.getenv("FLASK_SECRET_KEY", "kubeddos-dev-key")
    )

    def get_k8s_token(self) -> str:
        """Read the service account token from the mounted path."""
        try:
            with open(self.k8s_token_path) as f:
                return f.read().strip()
        except FileNotFoundError:
            return os.getenv("K8S_TOKEN", "")

    def get_k8s_ca(self) -> str:
        """Return CA cert path if it exists."""
        if os.path.exists(self.k8s_ca_path):
            return self.k8s_ca_path
        return ""
