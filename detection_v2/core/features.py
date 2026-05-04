"""
Deterministic feature extraction from a MetricSample.

Rules:
- FEATURE_NAMES defines the canonical feature order.  This list must not be
  reordered without retraining the model.
- extract_features() is a pure function: same input → same output, always.
- No fabricated statistics (no constant-fraction std approximations).
- All derived features are computable from MetricSample fields alone.
- Division denominators are guarded with a small epsilon to prevent zero-division.
"""

from __future__ import annotations

from typing import List

import numpy as np

from .schema import MetricSample
from .errors import FeatureValidationError

# ---------------------------------------------------------------------------
# Canonical feature list — order is fixed and must match training
# ---------------------------------------------------------------------------
FEATURE_NAMES: List[str] = [
    "request_rate",           # requests/s
    "request_rate_variance",  # variance of request rate across sub-windows
    "latency_p50_ms",
    "latency_p95_ms",
    "latency_p99_ms",
    "latency_spread_ms",      # p99 - p50 (tail-to-median gap)
    "error_ratio",            # error_rate / total_request_rate
    "byte_rate_in",           # bytes/s inbound
    "byte_rate_out",          # bytes/s outbound
    "byte_rate_asymmetry",    # out / (in + ε)  — high in flood attacks
    "avg_request_size_bytes",
    "avg_response_size_bytes",
    "size_ratio",             # response / (request + ε) — amplification indicator
    "connection_open_rate",   # new connections/s
    "net_connection_rate",    # open - close (accumulation indicator)
    "burstiness",             # variance / (rate + ε) — CoV variant
]

_EPSILON = 1e-9  # Prevents zero-division without distorting feature values


def extract_features(sample: MetricSample) -> np.ndarray:
    """
    Compute the canonical feature vector from a MetricSample.

    Returns:
        1-D numpy array of shape (len(FEATURE_NAMES),), dtype float64.

    Raises:
        FeatureValidationError: If the resulting vector contains NaN or Inf.
    """
    v = np.empty(len(FEATURE_NAMES), dtype=np.float64)

    v[0] = sample.request_rate
    v[1] = sample.request_rate_variance
    v[2] = sample.latency_p50_ms
    v[3] = sample.latency_p95_ms
    v[4] = sample.latency_p99_ms
    v[5] = sample.latency_p99_ms - sample.latency_p50_ms
    v[6] = sample.error_rate / max(sample.total_request_rate, _EPSILON)
    v[7] = sample.byte_rate_in
    v[8] = sample.byte_rate_out
    v[9] = sample.byte_rate_out / (sample.byte_rate_in + _EPSILON)
    v[10] = sample.avg_request_size_bytes
    v[11] = sample.avg_response_size_bytes
    v[12] = sample.avg_response_size_bytes / (sample.avg_request_size_bytes + _EPSILON)
    v[13] = sample.connection_open_rate
    v[14] = sample.connection_open_rate - sample.connection_close_rate
    v[15] = sample.request_rate_variance / (sample.request_rate + _EPSILON)

    # Validate output
    if np.any(np.isnan(v)) or np.any(np.isinf(v)):
        bad = [FEATURE_NAMES[i] for i in np.where(~np.isfinite(v))[0]]
        raise FeatureValidationError(
            f"Feature extraction produced non-finite values: {bad}"
        )

    return v


def validate_feature_vector(
    v: np.ndarray,
    *,
    max_abs_scaled_value: float = 15.0,
) -> None:
    """
    Validate a *scaled* feature vector before inference.

    Args:
        v: 1-D array of scaled features.
        max_abs_scaled_value: Alert threshold for out-of-distribution values.

    Raises:
        FeatureValidationError: On shape mismatch, NaN/Inf, or extreme values.
    """
    expected = len(FEATURE_NAMES)
    if v.shape != (expected,):
        raise FeatureValidationError(
            f"Expected feature vector of shape ({expected},), got {v.shape}"
        )
    if np.any(np.isnan(v)) or np.any(np.isinf(v)):
        raise FeatureValidationError("Scaled feature vector contains NaN or Inf")
    if np.any(np.abs(v) > max_abs_scaled_value):
        # Warning only — extreme values may be legitimate attacks
        import warnings
        n_extreme = int(np.sum(np.abs(v) > max_abs_scaled_value))
        warnings.warn(
            f"{n_extreme} scaled features exceed ±{max_abs_scaled_value}; "
            "model may extrapolate outside training distribution",
            RuntimeWarning,
            stacklevel=2,
        )
