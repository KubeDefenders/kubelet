"""
Default configuration for detection_v2.

Centralised, single source of truth.  All other modules read from these
values or accept overrides via constructor parameters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict


@dataclass(frozen=True)
class PrometheusConfig:
    url: str = "http://localhost:9090"
    namespace: str = "sock-shop"
    query_timeout: int = 10
    scrape_interval: int = 15
    max_consecutive_failures: int = 3


@dataclass(frozen=True)
class CollectionConfig:
    window_seconds: int = 30  # Must be >= 2× scrape_interval
    min_window_scrape_ratio: int = 2


@dataclass(frozen=True)
class ModelConfig:
    contamination: float = 0.05
    n_estimators: int = 200
    max_samples: int = 256
    random_state: int = 42


@dataclass(frozen=True)
class DetectionConfig:
    severity_critical: float = -0.7
    severity_high: float = -0.5
    severity_medium: float = -0.3
    min_consecutive_detections: int = 2
    alert_cooldown_seconds: int = 60


@dataclass(frozen=True)
class XAIConfig:
    shap_background_samples: int = 200
    max_display_features: int = 5


@dataclass(frozen=True)
class MonitorConfig:
    interval_seconds: int = 15
    window_seconds: int = 30
    alert_log_path: str = "logs/alerts.jsonl"


@dataclass(frozen=True)
class TrainingConfig:
    max_normal_samples: int = 50_000
    max_attack_samples: int = 5_000
    validation_split: float = 0.2
    random_state: int = 42


@dataclass(frozen=True)
class Config:
    """Top-level config aggregating all sub-configs."""
    prometheus: PrometheusConfig = field(default_factory=PrometheusConfig)
    collection: CollectionConfig = field(default_factory=CollectionConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    detection: DetectionConfig = field(default_factory=DetectionConfig)
    xai: XAIConfig = field(default_factory=XAIConfig)
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    training: TrainingConfig = field(default_factory=TrainingConfig)
