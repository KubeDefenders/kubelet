"""
Shared test fixtures.

Provide deterministic MetricSample instances, trained model artifacts,
and helper factories that all test modules can reuse.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import List

import numpy as np
import pytest

from detection_v2.core.schema import MetricSample
from detection_v2.core.features import FEATURE_NAMES, extract_features
from detection_v2.training.trainer import Trainer


# ----------------------------------------------------------------------- #
# MetricSample factories
# ----------------------------------------------------------------------- #

@pytest.fixture
def normal_sample() -> MetricSample:
    """A realistic normal-traffic MetricSample."""
    return MetricSample(
        timestamp=1700000000.0,
        request_rate=50.0,
        request_rate_variance=4.0,
        latency_p50_ms=12.0,
        latency_p95_ms=45.0,
        latency_p99_ms=120.0,
        error_rate=0.5,
        total_request_rate=50.0,
        byte_rate_in=25000.0,
        byte_rate_out=125000.0,
        avg_request_size_bytes=500.0,
        avg_response_size_bytes=2500.0,
        connection_open_rate=8.0,
        connection_close_rate=7.5,
    )


@pytest.fixture
def attack_sample() -> MetricSample:
    """A realistic attack-traffic MetricSample (high rate, high variance)."""
    return MetricSample(
        timestamp=1700000001.0,
        request_rate=5000.0,
        request_rate_variance=2000.0,
        latency_p50_ms=500.0,
        latency_p95_ms=3000.0,
        latency_p99_ms=8000.0,
        error_rate=400.0,
        total_request_rate=5000.0,
        byte_rate_in=5000000.0,
        byte_rate_out=500000.0,
        avg_request_size_bytes=100.0,
        avg_response_size_bytes=50.0,
        connection_open_rate=2000.0,
        connection_close_rate=200.0,
    )


@pytest.fixture
def zero_sample() -> MetricSample:
    """All-zero MetricSample (edge case: cold start / no traffic)."""
    return MetricSample.zero()


def make_normal_samples(n: int = 200, rng_seed: int = 42) -> List[MetricSample]:
    """Generate n synthetic normal-traffic samples with mild random variation."""
    rng = np.random.RandomState(rng_seed)
    samples = []
    for i in range(n):
        samples.append(MetricSample(
            timestamp=1700000000.0 + i,
            request_rate=max(0.0, 50.0 + rng.normal(0, 5)),
            request_rate_variance=max(0.0, 4.0 + rng.normal(0, 1)),
            latency_p50_ms=max(0.0, 12.0 + rng.normal(0, 2)),
            latency_p95_ms=max(0.0, 45.0 + rng.normal(0, 5)),
            latency_p99_ms=max(0.0, 120.0 + rng.normal(0, 10)),
            error_rate=max(0.0, 0.5 + rng.normal(0, 0.2)),
            total_request_rate=max(0.001, 50.0 + rng.normal(0, 5)),
            byte_rate_in=max(0.0, 25000.0 + rng.normal(0, 2000)),
            byte_rate_out=max(0.0, 125000.0 + rng.normal(0, 10000)),
            avg_request_size_bytes=max(0.0, 500.0 + rng.normal(0, 50)),
            avg_response_size_bytes=max(0.0, 2500.0 + rng.normal(0, 200)),
            connection_open_rate=max(0.0, 8.0 + rng.normal(0, 1)),
            connection_close_rate=max(0.0, 7.5 + rng.normal(0, 1)),
        ))
    return samples


def make_attack_samples(n: int = 50, rng_seed: int = 99) -> List[MetricSample]:
    """Generate n synthetic attack-traffic samples (extreme values)."""
    rng = np.random.RandomState(rng_seed)
    samples = []
    for i in range(n):
        samples.append(MetricSample(
            timestamp=1700100000.0 + i,
            request_rate=max(0.0, 5000.0 + rng.normal(0, 500)),
            request_rate_variance=max(0.0, 2000.0 + rng.normal(0, 300)),
            latency_p50_ms=max(0.0, 500.0 + rng.normal(0, 100)),
            latency_p95_ms=max(0.0, 3000.0 + rng.normal(0, 500)),
            latency_p99_ms=max(0.0, 8000.0 + rng.normal(0, 1000)),
            error_rate=max(0.0, 400.0 + rng.normal(0, 50)),
            total_request_rate=max(0.001, 5000.0 + rng.normal(0, 500)),
            byte_rate_in=max(0.0, 5000000.0 + rng.normal(0, 500000)),
            byte_rate_out=max(0.0, 500000.0 + rng.normal(0, 50000)),
            avg_request_size_bytes=max(0.0, 100.0 + rng.normal(0, 20)),
            avg_response_size_bytes=max(0.0, 50.0 + rng.normal(0, 10)),
            connection_open_rate=max(0.0, 2000.0 + rng.normal(0, 200)),
            connection_close_rate=max(0.0, 200.0 + rng.normal(0, 50)),
        ))
    return samples


@pytest.fixture
def trained_model_path(tmp_path: Path) -> Path:
    """Train a small model on synthetic data and return the artifact path."""
    normal = make_normal_samples(300)
    attack = make_attack_samples(50)

    trainer = Trainer(
        n_estimators=50,
        max_samples=64,
        shap_background_size=30,
    )
    trainer.fit(normal, attack_samples=attack)

    model_path = tmp_path / "test_model.pkl"
    trainer.save(model_path)
    return model_path
