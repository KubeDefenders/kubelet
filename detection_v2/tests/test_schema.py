"""Tests for MetricSample — validation, construction, edge cases."""

import math

import numpy as np
import pytest

from detection_v2.core.schema import MetricSample, Severity


class TestMetricSampleConstruction:
    def test_frozen(self, normal_sample):
        with pytest.raises(AttributeError):
            normal_sample.request_rate = 999.0  # type: ignore[misc]

    def test_zero_factory(self):
        s = MetricSample.zero()
        assert s.request_rate == 0.0
        assert s.timestamp > 0

    def test_fields_present(self, normal_sample):
        # Ensure all 14 metric fields exist
        assert normal_sample.request_rate == 50.0
        assert normal_sample.latency_p99_ms == 120.0


class TestMetricSampleValidation:
    def test_valid_sample_passes(self, normal_sample):
        normal_sample.validate()  # should not raise

    def test_zero_sample_passes(self, zero_sample):
        zero_sample.validate()

    def test_nan_rejected(self):
        s = MetricSample(
            timestamp=1.0,
            request_rate=float("nan"),
            request_rate_variance=0, latency_p50_ms=0, latency_p95_ms=0,
            latency_p99_ms=0, error_rate=0, total_request_rate=0,
            byte_rate_in=0, byte_rate_out=0,
            avg_request_size_bytes=0, avg_response_size_bytes=0,
            connection_open_rate=0, connection_close_rate=0,
        )
        with pytest.raises(ValueError, match="nan"):
            s.validate()

    def test_inf_rejected(self):
        s = MetricSample(
            timestamp=1.0,
            request_rate=float("inf"),
            request_rate_variance=0, latency_p50_ms=0, latency_p95_ms=0,
            latency_p99_ms=0, error_rate=0, total_request_rate=0,
            byte_rate_in=0, byte_rate_out=0,
            avg_request_size_bytes=0, avg_response_size_bytes=0,
            connection_open_rate=0, connection_close_rate=0,
        )
        with pytest.raises(ValueError, match="inf"):
            s.validate()

    def test_negative_rejected(self):
        s = MetricSample(
            timestamp=1.0,
            request_rate=-1.0,
            request_rate_variance=0, latency_p50_ms=0, latency_p95_ms=0,
            latency_p99_ms=0, error_rate=0, total_request_rate=0,
            byte_rate_in=0, byte_rate_out=0,
            avg_request_size_bytes=0, avg_response_size_bytes=0,
            connection_open_rate=0, connection_close_rate=0,
        )
        with pytest.raises(ValueError, match="negative"):
            s.validate()


class TestSeverity:
    def test_values(self):
        assert Severity.CRITICAL.value == "CRITICAL"
        assert Severity.LOW.value == "LOW"
