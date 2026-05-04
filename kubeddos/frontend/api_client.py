"""
Kubernetes and Prometheus API client for the KubeDDoS frontend.

Provides a synchronous interface for querying:
- CRD resources (CrossfireDetectionEvents, CrossfireMitigationIntents)
- Prometheus metrics (node utilization, pod traffic, invariant values)
- Cluster state (nodes, pods, deployments)

All methods are stateless and generic — no hardcoded namespaces or IPs.
"""

from __future__ import annotations

import json
import logging
import ssl
import time
from typing import Any, Dict, List, Optional, Tuple

import requests
import numpy as np

logger = logging.getLogger(__name__)


class KubeClient:
    """Synchronous Kubernetes API client for dashboard queries."""

    def __init__(self, api_url: str, token: str, ca_path: str = ""):
        self._api = api_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        self._verify: Any = ca_path if ca_path else False
        self._timeout = 10

    def _get(self, path: str) -> Optional[Dict]:
        try:
            resp = requests.get(
                f"{self._api}{path}",
                headers=self._headers,
                verify=self._verify,
                timeout=self._timeout,
            )
            if resp.status_code == 200:
                return resp.json()
            logger.debug("GET %s -> %d", path, resp.status_code)
            return None
        except Exception as e:
            logger.warning("GET %s error: %s", path, e)
            return None

    # ----- CRD Operations -----

    def list_detection_events(
        self, group: str = "crossfire.io", version: str = "v1alpha1"
    ) -> List[Dict]:
        data = self._get(f"/apis/{group}/{version}/crossfiredetectionevents")
        if data and "items" in data:
            return data["items"]
        return []

    def list_mitigation_intents(
        self, group: str = "crossfire.io", version: str = "v1alpha1"
    ) -> List[Dict]:
        data = self._get(f"/apis/{group}/{version}/crossfiremitigationintents")
        if data and "items" in data:
            return data["items"]
        return []

    def get_detection_event(
        self, name: str, group: str = "crossfire.io", version: str = "v1alpha1"
    ) -> Optional[Dict]:
        return self._get(f"/apis/{group}/{version}/crossfiredetectionevents/{name}")

    def get_mitigation_intent(
        self, name: str, group: str = "crossfire.io", version: str = "v1alpha1"
    ) -> Optional[Dict]:
        return self._get(f"/apis/{group}/{version}/crossfiremitigationintents/{name}")

    # ----- Cluster State -----

    def get_nodes(self) -> List[Dict]:
        data = self._get("/api/v1/nodes")
        if data and "items" in data:
            return data["items"]
        return []

    def get_pods(self, namespace: str = "") -> List[Dict]:
        path = f"/api/v1/namespaces/{namespace}/pods" if namespace else "/api/v1/pods"
        data = self._get(path)
        if data and "items" in data:
            return data["items"]
        return []

    def get_deployments(self, namespace: str = "") -> List[Dict]:
        path = (
            f"/apis/apps/v1/namespaces/{namespace}/deployments"
            if namespace
            else "/apis/apps/v1/deployments"
        )
        data = self._get(path)
        if data and "items" in data:
            return data["items"]
        return []

    def get_namespaces(self) -> List[str]:
        data = self._get("/api/v1/namespaces")
        if data and "items" in data:
            return [ns["metadata"]["name"] for ns in data["items"]]
        return []

    def get_crds(self) -> List[Dict]:
        data = self._get("/apis/apiextensions.k8s.io/v1/customresourcedefinitions")
        if data and "items" in data:
            return [
                c for c in data["items"]
                if "crossfire" in c.get("metadata", {}).get("name", "").lower()
            ]
        return []


class PrometheusClient:
    """Synchronous Prometheus query client for dashboard data."""

    def __init__(self, url: str, timeout: int = 10):
        self._url = url.rstrip("/")
        self._timeout = timeout

    def query(self, promql: str) -> Optional[List[Dict]]:
        try:
            resp = requests.get(
                f"{self._url}/api/v1/query",
                params={"query": promql},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") == "success":
                return data["data"]["result"]
            return None
        except Exception as e:
            logger.debug("Prometheus query failed: %s", e)
            return None

    def scalar(self, promql: str) -> Optional[float]:
        result = self.query(promql)
        if result:
            try:
                val = float(result[0]["value"][1])
                if np.isnan(val) or np.isinf(val):
                    return None
                return val
            except (KeyError, IndexError, ValueError):
                return None
        return None

    def query_range(
        self, promql: str, start: float, end: float, step: str = "15s"
    ) -> Optional[List[Dict]]:
        try:
            resp = requests.get(
                f"{self._url}/api/v1/query_range",
                params={
                    "query": promql,
                    "start": start,
                    "end": end,
                    "step": step,
                },
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") == "success":
                return data["data"]["result"]
            return None
        except Exception as e:
            logger.debug("Prometheus range query failed: %s", e)
            return None

    # ----- Convenience methods for invariant data -----

    def get_node_link_utilization(self, window: str = "60s") -> Dict[str, Dict]:
        """Get per-node, per-interface link utilization."""
        rx = self.query(
            f"rate(node_network_receive_bytes_total{{device!='lo'}}[{window}])"
        )
        tx = self.query(
            f"rate(node_network_transmit_bytes_total{{device!='lo'}}[{window}])"
        )
        nodes: Dict[str, Dict] = {}
        if not rx:
            return nodes

        rx_map = {}
        for r in rx:
            node = r["metric"].get("node", r["metric"].get("instance", "").split(":")[0])
            iface = r["metric"].get("device", "")
            try:
                rx_map[(node, iface)] = float(r["value"][1])
            except (KeyError, ValueError):
                pass

        tx_map = {}
        for r in (tx or []):
            node = r["metric"].get("node", r["metric"].get("instance", "").split(":")[0])
            iface = r["metric"].get("device", "")
            try:
                tx_map[(node, iface)] = float(r["value"][1])
            except (KeyError, ValueError):
                pass

        nic_capacity_bps = 1e9 / 8  # 1Gbps default
        for (node, iface), rx_bps in rx_map.items():
            tx_bps = tx_map.get((node, iface), 0.0)
            total_bps = rx_bps + tx_bps
            util_pct = min((total_bps / nic_capacity_bps) * 100.0, 100.0)
            if node not in nodes:
                nodes[node] = {}
            nodes[node][iface] = {
                "rx_mbps": rx_bps * 8 / 1e6,
                "tx_mbps": tx_bps * 8 / 1e6,
                "utilization_pct": round(util_pct, 2),
            }
        return nodes

    def get_pod_byte_rates(self, namespace: str, window: str = "60s") -> Dict[str, float]:
        """Get per-pod byte rates in a namespace."""
        result = self.query(
            f'sum by (pod) (rate(container_network_transmit_bytes_total'
            f'{{namespace="{namespace}"}}[{window}]))'
        )
        pods = {}
        for r in (result or []):
            pod = r["metric"].get("pod", "")
            try:
                pods[pod] = float(r["value"][1])
            except (KeyError, ValueError):
                pass
        return pods

    def get_service_error_rates(self, namespace: str, window: str = "60s") -> Dict[str, Dict]:
        """Get per-service error and request rates."""
        total_q = (
            f'sum by (destination_service_name) (rate(istio_requests_total'
            f'{{destination_service_namespace="{namespace}"}}[{window}]))'
        )
        error_q = (
            f'sum by (destination_service_name) (rate(istio_requests_total'
            f'{{destination_service_namespace="{namespace}",response_code=~"5.."}}[{window}]))'
        )
        total_r = self.query(total_q)
        error_r = self.query(error_q)

        services: Dict[str, Dict] = {}
        for r in (total_r or []):
            svc = r["metric"].get("destination_service_name", "")
            try:
                rate = float(r["value"][1])
                services[svc] = {"request_rate": rate, "error_rate": 0.0, "error_pct": 0.0}
            except (KeyError, ValueError):
                pass

        for r in (error_r or []):
            svc = r["metric"].get("destination_service_name", "")
            if svc in services:
                try:
                    err = float(r["value"][1])
                    services[svc]["error_rate"] = err
                    req = services[svc]["request_rate"]
                    services[svc]["error_pct"] = (err / req * 100) if req > 0 else 0.0
                except (KeyError, ValueError):
                    pass
        return services

    def get_latency_percentiles(self, namespace: str, window: str = "60s") -> Dict[str, Dict]:
        """Get p50/p95/p99 latency per service."""
        services: Dict[str, Dict] = {}
        for pct, label in [(0.50, "p50"), (0.95, "p95"), (0.99, "p99")]:
            q = (
                f'histogram_quantile({pct}, sum by (le, destination_service_name) '
                f'(rate(istio_request_duration_milliseconds_bucket'
                f'{{destination_service_namespace="{namespace}"}}[{window}])) )'
            )
            result = self.query(q)
            for r in (result or []):
                svc = r["metric"].get("destination_service_name", "")
                try:
                    val = float(r["value"][1])
                    if svc not in services:
                        services[svc] = {}
                    services[svc][label] = round(val, 2)
                except (KeyError, ValueError):
                    pass
        return services

    def check_connectivity(self) -> bool:
        """Check if Prometheus is reachable."""
        try:
            resp = requests.get(
                f"{self._url}/api/v1/status/config",
                timeout=5,
            )
            return resp.status_code == 200
        except Exception:
            return False
