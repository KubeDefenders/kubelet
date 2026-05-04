"""
Prometheus / Istio metric adapter.

Translates Prometheus PromQL queries into generic MetricSample objects.
This is the *only* place that Prometheus URLs, Istio metric names, or
Kubernetes namespaces appear.
"""

from __future__ import annotations

import time
import logging
from collections import deque
from typing import Dict, Optional

import numpy as np
import requests

from ..core.schema import MetricSample
from ..core.errors import MetricCollectionError

logger = logging.getLogger(__name__)


class PrometheusAdapter:
    """
    Collects Istio telemetry from a Prometheus endpoint and produces
    generic MetricSample objects.

    The namespace, query timeout, and window size are constructor
    parameters — nothing is hardcoded.
    """

    def __init__(
        self,
        prometheus_url: str = "http://localhost:9090",
        namespace: str = "sock-shop",
        query_timeout: int = 10,
        max_consecutive_failures: int = 3,
    ):
        self.url = prometheus_url.rstrip("/")
        self.namespace = namespace
        self.timeout = query_timeout
        self._max_failures = max_consecutive_failures
        self._consecutive_failures = 0

        # Ring buffer of recent request-rate samples for computing variance
        self._rate_history: deque[float] = deque(maxlen=30)

    def reset_history(self) -> None:
        """Clear the rate history ring buffer.

        Useful after known regime changes (e.g. attack end) to prevent
        stale high-variance samples from poisoning the burstiness feature.
        """
        self._rate_history.clear()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def collect(self, window_seconds: int = 30) -> MetricSample:
        """
        Query Prometheus and build a MetricSample.

        Args:
            window_seconds: The ``rate()`` / ``increase()`` window.
                Must be >= 2× the Prometheus scrape interval (typically 15 s).

        Returns:
            MetricSample with current telemetry.

        Raises:
            MetricCollectionError: When too many queries fail in one round
                or over consecutive rounds.
        """
        queries = self._build_queries(window_seconds)
        results: Dict[str, Optional[float]] = {}
        failures = 0

        for name, query in queries.items():
            val = self._query(query)
            if val is None:
                failures += 1
                results[name] = 0.0
            else:
                results[name] = val

        # Health bookkeeping
        if failures > len(queries) // 2:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._max_failures:
                raise MetricCollectionError(
                    f"Prometheus unreliable: {failures}/{len(queries)} queries "
                    f"failed ({self._consecutive_failures} consecutive rounds)"
                )
            logger.warning(
                "Prometheus partially degraded: %d/%d queries failed (round %d/%d)",
                failures, len(queries),
                self._consecutive_failures, self._max_failures,
            )
        else:
            if self._consecutive_failures > 0:
                logger.info("Prometheus connectivity recovered")
            self._consecutive_failures = 0

        return self._to_metric_sample(results)

    # ------------------------------------------------------------------ #
    # PromQL query construction
    # ------------------------------------------------------------------ #

    def _build_queries(self, w: int) -> Dict[str, str]:
        ns = self.namespace
        ws = f"{w}s"
        return {
            "request_rate": (
                f'sum(rate(istio_requests_total'
                f'{{destination_service_namespace="{ns}"}}[{ws}]))'
            ),
            "error_rate": (
                f'sum(rate(istio_requests_total'
                f'{{destination_service_namespace="{ns}",'
                f'response_code=~"5.."}}[{ws}]))'
            ),
            "latency_p50": (
                f'histogram_quantile(0.50, sum(rate('
                f'istio_request_duration_milliseconds_bucket'
                f'{{destination_service_namespace="{ns}"}}[{ws}])) by (le))'
            ),
            "latency_p95": (
                f'histogram_quantile(0.95, sum(rate('
                f'istio_request_duration_milliseconds_bucket'
                f'{{destination_service_namespace="{ns}"}}[{ws}])) by (le))'
            ),
            "latency_p99": (
                f'histogram_quantile(0.99, sum(rate('
                f'istio_request_duration_milliseconds_bucket'
                f'{{destination_service_namespace="{ns}"}}[{ws}])) by (le))'
            ),
            "byte_rate_in": (
                f'sum(rate(istio_request_bytes_sum'
                f'{{destination_service_namespace="{ns}"}}[{ws}]))'
            ),
            "byte_rate_out": (
                f'sum(rate(istio_response_bytes_sum'
                f'{{destination_service_namespace="{ns}"}}[{ws}]))'
            ),
            "avg_request_size": (
                f'sum(rate(istio_request_bytes_sum'
                f'{{destination_service_namespace="{ns}"}}[{ws}])) / '
                f'sum(rate(istio_request_bytes_count'
                f'{{destination_service_namespace="{ns}"}}[{ws}]))'
            ),
            "avg_response_size": (
                f'sum(rate(istio_response_bytes_sum'
                f'{{destination_service_namespace="{ns}"}}[{ws}])) / '
                f'sum(rate(istio_response_bytes_count'
                f'{{destination_service_namespace="{ns}"}}[{ws}]))'
            ),
            "conn_opened": (
                f'sum(rate(istio_tcp_connections_opened_total'
                f'{{destination_service_namespace="{ns}"}}[{ws}]))'
            ),
            "conn_closed": (
                f'sum(rate(istio_tcp_connections_closed_total'
                f'{{destination_service_namespace="{ns}"}}[{ws}]))'
            ),
        }

    # ------------------------------------------------------------------ #
    # Prometheus HTTP query
    # ------------------------------------------------------------------ #

    def _query(self, query: str) -> Optional[float]:
        """Execute a single PromQL query.  Returns None on failure."""
        try:
            resp = requests.post(
                f"{self.url}/api/v1/query",
                data={"query": query},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            if data["status"] == "success" and data["data"]["result"]:
                val = float(data["data"]["result"][0]["value"][1])
                if np.isnan(val) or np.isinf(val):
                    return None
                return val
            return None
        except Exception as exc:
            logger.debug("Prometheus query failed: %s — %s", query[:80], exc)
            return None

    # ------------------------------------------------------------------ #
    # MetricSample construction
    # ------------------------------------------------------------------ #

    def _to_metric_sample(self, r: Dict[str, float]) -> MetricSample:
        """Convert raw query results to a MetricSample."""
        req_rate = r.get("request_rate", 0.0) or 0.0

        # Compute request-rate variance from ring buffer of recent rates
        self._rate_history.append(req_rate)
        if len(self._rate_history) >= 3:
            variance = float(np.var(list(self._rate_history)))
        else:
            variance = 0.0

        return MetricSample(
            timestamp=time.time(),
            request_rate=req_rate,
            request_rate_variance=variance,
            latency_p50_ms=r.get("latency_p50", 0.0) or 0.0,
            latency_p95_ms=r.get("latency_p95", 0.0) or 0.0,
            latency_p99_ms=r.get("latency_p99", 0.0) or 0.0,
            error_rate=r.get("error_rate", 0.0) or 0.0,
            total_request_rate=req_rate,
            byte_rate_in=r.get("byte_rate_in", 0.0) or 0.0,
            byte_rate_out=r.get("byte_rate_out", 0.0) or 0.0,
            avg_request_size_bytes=r.get("avg_request_size", 0.0) or 0.0,
            avg_response_size_bytes=r.get("avg_response_size", 0.0) or 0.0,
            connection_open_rate=r.get("conn_opened", 0.0) or 0.0,
            connection_close_rate=r.get("conn_closed", 0.0) or 0.0,
        )
