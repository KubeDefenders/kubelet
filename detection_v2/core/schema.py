"""
Data contracts for the detection pipeline.

All public interfaces accept or produce these types.
No external dependencies — pure Python dataclasses.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple

import numpy as np


class Severity(Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class MetricSample:
    """
    Generic representation of a single metric collection window.

    This type is intentionally decoupled from Prometheus, Istio, and any
    specific namespace.  All values are in SI-compatible units (seconds,
    bytes, dimensionless ratios) or in milliseconds where noted.

    Callers must supply a timestamp; use ``time.time()`` for live data.
    """

    # Temporal
    timestamp: float  # Unix epoch seconds (float)

    # Request throughput
    request_rate: float           # Requests per second over the collection window
    request_rate_variance: float  # Variance of per-sub-window request rate (≥ 0)

    # Latency (milliseconds)
    latency_p50_ms: float
    latency_p95_ms: float
    latency_p99_ms: float

    # Errors
    error_rate: float          # Error requests per second (e.g. 5xx)
    total_request_rate: float  # Total requests/s (denominator for ratio)

    # Byte throughput
    byte_rate_in: float   # Inbound bytes per second (request bodies)
    byte_rate_out: float  # Outbound bytes per second (response bodies)

    # Packet/message sizes
    avg_request_size_bytes: float
    avg_response_size_bytes: float

    # Connection lifecycle
    connection_open_rate: float   # New connections per second
    connection_close_rate: float  # Closed connections per second

    def validate(self) -> None:
        """Raise ValueError if any field is NaN, Inf, or out of domain."""
        for fname, fval in self.__dataclass_fields__.items():  # type: ignore[attr-defined]
            v = getattr(self, fname)
            if fname == "timestamp":
                continue
            if not isinstance(v, (int, float)):
                raise ValueError(f"MetricSample.{fname} must be numeric, got {type(v)}")
            if np.isnan(v) or np.isinf(v):
                raise ValueError(f"MetricSample.{fname} is {v}")
            if v < 0:
                raise ValueError(f"MetricSample.{fname} is negative ({v})")

    @classmethod
    def zero(cls) -> "MetricSample":
        """Return a zeroed-out sample (useful for testing)."""
        return cls(
            timestamp=time.time(),
            request_rate=0.0,
            request_rate_variance=0.0,
            latency_p50_ms=0.0,
            latency_p95_ms=0.0,
            latency_p99_ms=0.0,
            error_rate=0.0,
            total_request_rate=0.0,
            byte_rate_in=0.0,
            byte_rate_out=0.0,
            avg_request_size_bytes=0.0,
            avg_response_size_bytes=0.0,
            connection_open_rate=0.0,
            connection_close_rate=0.0,
        )


@dataclass(frozen=True)
class FeatureContribution:
    """SHAP contribution for a single feature."""

    feature_name: str
    shap_value: float   # Raw SHAP value (signed)
    feature_value: float  # Pre-scaling feature value


@dataclass(frozen=True)
class Explanation:
    """
    SHAP-based explanation for an anomaly detection decision.

    Invariant (verifiable by callers):
        sum(c.shap_value for c in contributions) + base_value ≈ model_output_score
    """

    contributions: Tuple[FeatureContribution, ...]  # All features, sorted |shap| desc
    base_value: float       # SHAP expected value (mean model output over background)
    model_output: float     # Raw anomaly score from the model

    @property
    def top(self, n: int = 5) -> Tuple[FeatureContribution, ...]:
        """Return the n highest-magnitude contributions."""
        return self.contributions[:n]


@dataclass(frozen=True)
class DetectionResult:
    """
    Immutable output of a single inference pass.

    ``explanation`` is ``None`` when ``is_anomaly`` is False (or when xAI
    is disabled by the caller).
    """

    is_anomaly: bool
    anomaly_score: float    # Higher = more normal (Isolation Forest convention)
    severity: Severity
    timestamp: float        # Mirrors MetricSample.timestamp
    feature_names: Tuple[str, ...]
    scaled_features: np.ndarray  # Shape (n_features,) — post-scaling values
    explanation: Optional[Explanation] = None

    # Metadata
    model_version: str = ""
    detection_latency_ms: float = 0.0

    def summary(self) -> str:
        """One-line human-readable summary."""
        status = "ANOMALY" if self.is_anomaly else "NORMAL"
        return (
            f"{status} | score={self.anomaly_score:+.3f} | "
            f"severity={self.severity.value} | "
            f"latency={self.detection_latency_ms:.1f}ms"
        )
