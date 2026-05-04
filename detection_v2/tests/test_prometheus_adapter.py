"""Tests for the Prometheus adapter — query building, health tracking, sample construction."""

import time
from unittest.mock import MagicMock, patch

import pytest

from detection_v2.adapters.prometheus_adapter import PrometheusAdapter
from detection_v2.core.schema import MetricSample
from detection_v2.core.errors import MetricCollectionError


class TestQueryBuilding:
    def test_queries_contain_namespace(self):
        adapter = PrometheusAdapter(namespace="my-namespace")
        queries = adapter._build_queries(30)

        for name, q in queries.items():
            assert 'my-namespace' in q, (
                f"Query '{name}' does not contain namespace parameter"
            )

    def test_queries_contain_window(self):
        adapter = PrometheusAdapter()
        queries = adapter._build_queries(60)

        for name, q in queries.items():
            assert "60s" in q, f"Query '{name}' does not contain window"

    def test_default_namespace_is_sock_shop(self):
        adapter = PrometheusAdapter()
        queries = adapter._build_queries(30)
        assert "sock-shop" in queries["request_rate"]

    def test_all_expected_queries_present(self):
        adapter = PrometheusAdapter()
        queries = adapter._build_queries(30)
        expected_keys = {
            "request_rate", "error_rate",
            "latency_p50", "latency_p95", "latency_p99",
            "byte_rate_in", "byte_rate_out",
            "avg_request_size", "avg_response_size",
            "conn_opened", "conn_closed",
        }
        assert set(queries.keys()) == expected_keys


class TestSampleConstruction:
    def test_to_metric_sample_returns_valid_sample(self):
        adapter = PrometheusAdapter()
        raw = {
            "request_rate": 100.0,
            "error_rate": 2.0,
            "latency_p50": 10.0,
            "latency_p95": 50.0,
            "latency_p99": 120.0,
            "byte_rate_in": 5000.0,
            "byte_rate_out": 50000.0,
            "avg_request_size": 200.0,
            "avg_response_size": 1000.0,
            "conn_opened": 15.0,
            "conn_closed": 14.0,
        }
        sample = adapter._to_metric_sample(raw)

        assert isinstance(sample, MetricSample)
        assert sample.request_rate == 100.0
        assert sample.latency_p50_ms == 10.0
        assert sample.connection_open_rate == 15.0
        sample.validate()  # should not raise

    def test_missing_keys_default_to_zero(self):
        adapter = PrometheusAdapter()
        sample = adapter._to_metric_sample({})

        assert sample.request_rate == 0.0
        assert sample.latency_p99_ms == 0.0
        sample.validate()

    def test_variance_computed_from_history(self):
        adapter = PrometheusAdapter()

        # Feed several rate samples
        for rate in [10.0, 20.0, 30.0, 40.0, 50.0]:
            sample = adapter._to_metric_sample({"request_rate": rate})

        # After multiple samples, variance should be > 0
        assert sample.request_rate_variance > 0.0


class TestHealthTracking:
    @patch.object(PrometheusAdapter, "_query", return_value=None)
    def test_consecutive_failures_raise(self, mock_query):
        adapter = PrometheusAdapter(max_consecutive_failures=2)

        # Round 1: all queries fail → warning but no error yet
        with pytest.raises(MetricCollectionError):
            # Need 2 consecutive rounds of majority failure
            adapter.collect(30)  # round 1 (partial)
            adapter.collect(30)  # round 2 → should raise

    @patch.object(PrometheusAdapter, "_query", return_value=42.0)
    def test_successful_queries_reset_counter(self, mock_query):
        adapter = PrometheusAdapter(max_consecutive_failures=3)
        adapter._consecutive_failures = 2  # simulate prior failures

        sample = adapter.collect(30)
        assert adapter._consecutive_failures == 0
        assert isinstance(sample, MetricSample)
