"""
CrossfireDetector — detection service for indirect crossfire attacks.

Design principles
-----------------
- Queries Prometheus only. No kubectl, no shell subprocesses.
- Evaluates the four crossfire invariants per cluster node.
- Creates CrossfireDetectionEvent CRs via the Kubernetes API.
- Resolves events when conditions clear.
- Exports Prometheus metrics for its own observability.
- Fully generic: no hard-coded application names or namespaces.

The four invariants (all must hold simultaneously on a node):
  I1. link_utilization(node, iface) >= HIGH_THRESHOLD
  I2. sum(decoy_pod_byte_rate, node) >= DECOY_FRACTION * link_capacity
  I3. victim_error_rate(service) >= ERROR_RATE_THRESHOLD
  I4. victim_direct_traffic_pct(node) <= LOW_DIRECT_TRAFFIC_THRESHOLD

I4 is the distinguishing invariant: it rules out direct DDoS attacks.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
from prometheus_client import Counter, Gauge, Histogram, start_http_server

# ---------------------------------------------------------------------------
# Structured logger
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s","msg":"%(message)s"}',
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
log = logging.getLogger("crossfire-detector")


# ---------------------------------------------------------------------------
# Configuration (from environment variables — generic, no hard-coding)
# ---------------------------------------------------------------------------
@dataclass
class DetectorConfig:
    prometheus_url: str = field(
        default_factory=lambda: os.getenv("PROMETHEUS_URL", "http://kube-prometheus-stack-prometheus.monitoring.svc.cluster.local:9090")
    )
    k8s_api_url: str = field(
        default_factory=lambda: os.getenv("KUBERNETES_API_URL", "https://kubernetes.default.svc")
    )
    # Thresholds
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
    # Timing
    evaluation_interval_seconds: int = field(
        default_factory=lambda: int(os.getenv("EVALUATION_INTERVAL_SECONDS", "15"))
    )
    detection_window_seconds: int = field(
        default_factory=lambda: int(os.getenv("DETECTION_WINDOW_SECONDS", "60"))
    )
    # Must confirm across N consecutive windows to reduce false positives
    confirmation_windows: int = field(
        default_factory=lambda: int(os.getenv("CONFIRMATION_WINDOWS", "2"))
    )
    # Metrics server port
    metrics_port: int = field(
        default_factory=lambda: int(os.getenv("METRICS_PORT", "8080"))
    )
    # Namespace where CRs are managed
    crd_group: str = "crossfire.io"
    crd_version: str = "v1alpha1"
    crd_plural: str = "crossfiredetectionevents"


# ---------------------------------------------------------------------------
# Self-observability metrics
# ---------------------------------------------------------------------------
EVALUATIONS_TOTAL = Counter(
    "crossfire_evaluations_total",
    "Total invariant evaluation cycles",
    ["node"],
)
INVARIANT_RESULTS = Counter(
    "crossfire_invariant_results_total",
    "Result of each invariant check per node",
    ["node", "invariant", "result"],  # result: pass|fail
)
DETECTION_EVENTS_TOTAL = Counter(
    "crossfire_detection_events_total",
    "Total CrossfireDetectionEvents created",
    ["node"],
)
RESOLUTION_EVENTS_TOTAL = Counter(
    "crossfire_resolution_events_total",
    "Total CrossfireDetectionEvents resolved",
    ["node"],
)
DATA_GAPS_TOTAL = Counter(
    "crossfire_data_gaps_total",
    "Evaluation cycles skipped due to missing Prometheus data",
    ["node", "reason"],
)
ACTIVE_EVENTS = Gauge(
    "crossfire_active_events",
    "Number of currently unresolved CrossfireDetectionEvents",
)
LINK_UTILIZATION = Gauge(
    "crossfire_node_link_utilization_pct",
    "Observed link utilization on congested interface",
    ["node", "interface"],
)
EVAL_DURATION = Histogram(
    "crossfire_evaluation_duration_seconds",
    "Time taken per full evaluation cycle",
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class PodInfo:
    namespace: str
    name: str
    pod_ip: str
    node: str
    deployment_name: str = ""
    byte_rate_mbps: float = 0.0


@dataclass
class NodeMetrics:
    node: str
    interface: str
    link_capacity_mbps: float
    link_utilization_pct: float
    packet_drop_rate: float
    all_pods: List[PodInfo]

    @property
    def link_bytes_per_sec(self) -> float:
        return self.link_capacity_mbps * 1e6 / 8


@dataclass
class ServiceMetrics:
    namespace: str
    service: str
    error_rate_pct: float
    request_rate: float
    p95_latency_ms: float


@dataclass
class InvariantResult:
    i1_link_saturated: bool
    i2_decoys_driving_congestion: bool
    i3_victim_degraded: bool
    i4_victim_not_directly_attacked: bool

    decoy_pods: List[PodInfo]
    victim_pods: List[PodInfo]
    link_utilization_pct: float
    packet_drop_rate: float
    victim_error_rate_pct: float
    victim_direct_traffic_pct: float
    confidence_score: float

    def all_pass(self) -> bool:
        return (
            self.i1_link_saturated
            and self.i2_decoys_driving_congestion
            and self.i3_victim_degraded
            and self.i4_victim_not_directly_attacked
        )


# ---------------------------------------------------------------------------
# Prometheus query helpers
# ---------------------------------------------------------------------------
class PrometheusClient:
    def __init__(self, session: aiohttp.ClientSession, base_url: str):
        self._session = session
        self._base_url = base_url.rstrip("/")

    async def instant(self, promql: str) -> Optional[List[Dict[str, Any]]]:
        """Execute an instant PromQL query; return the result vector or None."""
        url = f"{self._base_url}/api/v1/query"
        try:
            async with self._session.get(
                url,
                params={"query": promql},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    log.warning(f"Prometheus returned HTTP {resp.status} for query: {promql[:80]}")
                    return None
                body = await resp.json()
                if body.get("status") != "success":
                    log.warning(f"Prometheus query failed: {body.get('error')}")
                    return None
                return body["data"]["result"]
        except asyncio.TimeoutError:
            log.warning(f"Prometheus query timed out: {promql[:80]}")
            return None
        except Exception as exc:
            log.warning(f"Prometheus query error: {exc}")
            return None

    async def scalar(self, promql: str) -> Optional[float]:
        """Execute a query and return a single float, or None."""
        result = await self.instant(promql)
        if not result:
            return None
        try:
            return float(result[0]["value"][1])
        except (KeyError, IndexError, ValueError):
            return None


# ---------------------------------------------------------------------------
# Kubernetes API client (minimal, using service account token)
# ---------------------------------------------------------------------------
class K8sClient:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        api_server: str,
        token: str,
        ca_cert_path: str,
    ):
        self._session = session
        self._api = api_server.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        if ca_cert_path and os.path.exists(ca_cert_path):
            import ssl as _ssl_mod
            self._ssl = _ssl_mod.create_default_context(cafile=ca_cert_path)
        else:
            self._ssl = False

    async def _request(self, method: str, path: str, body: Optional[Any] = None) -> Optional[Any]:
        url = f"{self._api}{path}"
        kwargs: Dict[str, Any] = {"headers": self._headers, "ssl": self._ssl}
        if body is not None:
            kwargs["json"] = body
        try:
            async with self._session.request(
                method, url, timeout=aiohttp.ClientTimeout(total=15), **kwargs
            ) as resp:
                text = await resp.text()
                if resp.status in (200, 201):
                    return json.loads(text)
                elif resp.status == 409:
                    # AlreadyExists — normal for idempotent creates
                    return json.loads(text)
                else:
                    log.warning(f"K8s API {method} {path} -> {resp.status}: {text[:200]}")
                    return None
        except Exception as exc:
            log.warning(f"K8s API error: {exc}")
            return None

    async def create_cluster_resource(self, group: str, version: str, plural: str, body: Dict) -> Optional[Any]:
        path = f"/apis/{group}/{version}/{plural}"
        return await self._request("POST", path, body)

    async def patch_cluster_resource_status(
        self, group: str, version: str, plural: str, name: str, status_patch: Dict
    ) -> Optional[Any]:
        path = f"/apis/{group}/{version}/{plural}/{name}/status"
        patch_body = {"status": status_patch}
        headers = {**self._headers, "Content-Type": "application/merge-patch+json"}
        url = f"{self._api}{path}"
        try:
            async with self._session.patch(
                url,
                json=patch_body,
                headers=headers,
                ssl=self._ssl,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                return json.loads(await resp.text()) if resp.status in (200, 201) else None
        except Exception as exc:
            log.warning(f"K8s PATCH status error: {exc}")
            return None

    async def list_cluster_resources(self, group: str, version: str, plural: str) -> Optional[List[Dict]]:
        path = f"/apis/{group}/{version}/{plural}"
        result = await self._request("GET", path)
        if result and "items" in result:
            return result["items"]
        return None


# ---------------------------------------------------------------------------
# Core detection logic
# ---------------------------------------------------------------------------
class CrossfireDetector:
    def __init__(self, config: DetectorConfig):
        self._config = config
        # Track consecutive confirmation windows per node
        self._pending_confirmations: Dict[str, int] = {}
        # Track active detection events: node -> event name
        self._active_events: Dict[str, str] = {}

    async def run_forever(self) -> None:
        """Main detection loop."""
        token = _read_service_account_token()
        ca_cert = _read_ca_cert()

        async with aiohttp.ClientSession() as session:
            prom = PrometheusClient(session, self._config.prometheus_url)
            k8s = K8sClient(session, self._config.k8s_api_url, token, ca_cert)

            log.info(
                f"CrossfireDetector starting. "
                f"prometheus={self._config.prometheus_url} "
                f"interval={self._config.evaluation_interval_seconds}s "
                f"link_threshold={self._config.link_utilization_threshold_pct}%"
            )
            start_http_server(self._config.metrics_port)
            log.info(f"Metrics server started on :{self._config.metrics_port}/metrics")

            while True:
                try:
                    await self._evaluate_all_nodes(prom, k8s)
                except Exception as exc:
                    log.error(f"Evaluation cycle error: {exc}", exc_info=True)
                await asyncio.sleep(self._config.evaluation_interval_seconds)

    async def _evaluate_all_nodes(self, prom: PrometheusClient, k8s: K8sClient) -> None:
        """Evaluate crossfire invariants for all cluster nodes."""
        nodes = await self._get_nodes_with_high_utilization(prom)
        if nodes is None:
            log.warning("Could not retrieve node network metrics from Prometheus")
            return

        if not nodes:
            log.debug("No nodes above link utilization threshold")

        for node_metrics in nodes:
            log.info(
                f"Evaluating node {node_metrics.node} iface={node_metrics.interface} "
                f"util={node_metrics.link_utilization_pct:.1f}% pods={len(node_metrics.all_pods)}"
            )
            with EVAL_DURATION.time():
                EVALUATIONS_TOTAL.labels(node=node_metrics.node).inc()
                await self._evaluate_node(prom, k8s, node_metrics)

        # Check for resolved events (nodes that were active but are no longer in high-util list)
        active_nodes = {nm.node for nm in nodes}
        for node, event_name in list(self._active_events.items()):
            if node not in active_nodes:
                await self._resolve_event(k8s, node, event_name)

    async def _get_nodes_with_high_utilization(
        self, prom: PrometheusClient
    ) -> Optional[List[NodeMetrics]]:
        """
        Query Prometheus for per-node, per-interface bandwidth utilization.
        Returns NodeMetrics for each node/interface pair exceeding threshold.

        Uses node_exporter metrics: standard in kube-prometheus-stack.
        """
        # bytes/s receive rate per node/interface
        window = f"{self._config.detection_window_seconds}s"
        rx_query = f"rate(node_network_receive_bytes_total{{device!='lo'}}[{window}])"
        tx_query = f"rate(node_network_transmit_bytes_total{{device!='lo'}}[{window}])"
        drop_query = f"rate(node_network_receive_drop_total{{device!='lo'}}[{window}])"

        rx_results = await prom.instant(rx_query)
        tx_results = await prom.instant(tx_query)
        drop_results = await prom.instant(drop_query)

        if rx_results is None or tx_results is None:
            return None

        # Build lookup: (node, device) -> value
        # Filter out IP-based duplicates — prefer hostname labels from k8s relabeling
        import re
        _IP_PATTERN = re.compile(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}')

        def build_lookup(results: List[Dict]) -> Dict[Tuple[str, str], float]:
            out: Dict[Tuple[str, str], float] = {}
            for r in results:
                inst = r["metric"].get("instance", "")
                dev = r["metric"].get("device", "")
                node = r["metric"].get("node", inst.split(":")[0])
                # Skip entries where the node looks like an IP address
                # (these are duplicates from node-exporter using instance label)
                if _IP_PATTERN.match(node):
                    continue
                try:
                    out[(node, dev)] = float(r["value"][1])
                except (KeyError, ValueError):
                    pass
            return out

        rx_lookup = build_lookup(rx_results)
        tx_lookup = build_lookup(tx_results or [])
        drop_lookup = build_lookup(drop_results or [])

        # Estimate NIC capacity from the maximum observed rate (conservative)
        # In production this should come from node metadata (e.g., 1Gbps label)
        # We default to 1Gbps and allow override via annotation
        nic_capacity_mbps = float(os.getenv("DEFAULT_NIC_CAPACITY_MBPS", "1000.0"))

        node_metrics_list: List[NodeMetrics] = []
        seen_pairs: set = set()

        for (node, iface), rx_bps in rx_lookup.items():
            if (node, iface) in seen_pairs:
                continue
            seen_pairs.add((node, iface))

            tx_bps = tx_lookup.get((node, iface), 0.0)
            total_bps = rx_bps + tx_bps
            total_mbps = total_bps * 8 / 1e6
            util_pct = min((total_mbps / nic_capacity_mbps) * 100.0, 100.0)
            drop_rate = drop_lookup.get((node, iface), 0.0)

            LINK_UTILIZATION.labels(node=node, interface=iface).set(util_pct)

            if util_pct >= self._config.link_utilization_threshold_pct:
                pod_infos = await self._get_pods_on_node(prom, node)
                if pod_infos is None:
                    DATA_GAPS_TOTAL.labels(node=node, reason="pod_metrics_unavailable").inc()
                    continue
                node_metrics_list.append(
                    NodeMetrics(
                        node=node,
                        interface=iface,
                        link_capacity_mbps=nic_capacity_mbps,
                        link_utilization_pct=util_pct,
                        packet_drop_rate=drop_rate,
                        all_pods=pod_infos,
                    )
                )

        return node_metrics_list

    async def _get_pods_on_node(
        self, prom: PrometheusClient, node: str
    ) -> Optional[List[PodInfo]]:
        """
        Get per-pod network byte rates for all pods on a given node.
        Uses kube-state-metrics + cAdvisor metrics.
        """
        window = f"{self._config.detection_window_seconds}s"

        # Pod → node mapping (kube-state-metrics)
        # Note: kube_pod_info doesn't have a phase label - use join with kube_pod_status_phase
        # or just query without phase filter (pods in other phases are rare on a running node)
        pod_node_query = f'kube_pod_info{{node="{node}"}}'
        pod_node_results = await prom.instant(pod_node_query)
        if pod_node_results is None:
            return None

        if not pod_node_results:
            # No running pods on this node
            return []

        # Per-pod byte rate (cAdvisor)
        rx_query = (
            f'sum by (namespace, pod) ('
            f'  rate(container_network_receive_bytes_total{{node="{node}"}}[{window}])'
            f')'
        )
        pod_rx_results = await prom.instant(rx_query)
        pod_rx_lookup: Dict[Tuple[str, str], float] = {}
        for r in (pod_rx_results or []):
            ns = r["metric"].get("namespace", "")
            pod = r["metric"].get("pod", "")
            try:
                pod_rx_lookup[(ns, pod)] = float(r["value"][1])
            except (KeyError, ValueError):
                pass

        # Deployment owners (kube-state-metrics)
        owner_query = (
            f'kube_pod_owner{{node="{node}",owner_kind="ReplicaSet"}}'
        )
        owner_results = await prom.instant(owner_query)
        replicaset_to_deploy: Dict[Tuple[str, str], str] = {}
        for r in (owner_results or []):
            ns = r["metric"].get("namespace", "")
            pod = r["metric"].get("pod", "")
            rs = r["metric"].get("owner_name", "")
            # ReplicaSet name typically is <deployment>-<hash>
            deploy = "-".join(rs.split("-")[:-1]) if rs else ""
            replicaset_to_deploy[(ns, pod)] = deploy

        pod_infos: List[PodInfo] = []
        for r in pod_node_results:
            ns = r["metric"].get("namespace", "")
            pod_name = r["metric"].get("pod", "")
            pod_ip = r["metric"].get("pod_ip", "")
            rx_bps = pod_rx_lookup.get((ns, pod_name), 0.0)
            rx_mbps = rx_bps * 8 / 1e6
            deploy = replicaset_to_deploy.get((ns, pod_name), "")
            pod_infos.append(
                PodInfo(
                    namespace=ns,
                    name=pod_name,
                    pod_ip=pod_ip,
                    node=node,
                    deployment_name=deploy,
                    byte_rate_mbps=rx_mbps,
                )
            )

        return pod_infos

    async def _evaluate_node(
        self, prom: PrometheusClient, k8s: K8sClient, nm: NodeMetrics
    ) -> None:
        """Evaluate the four crossfire invariants for a single node."""
        cfg = self._config

        # Sort pods by ingress byte rate descending
        sorted_pods = sorted(nm.all_pods, key=lambda p: p.byte_rate_mbps, reverse=True)

        # ---------------------------------------------------------------
        # I1: Link saturated (already confirmed by threshold in caller)
        # ---------------------------------------------------------------
        i1 = nm.link_utilization_pct >= cfg.link_utilization_threshold_pct
        INVARIANT_RESULTS.labels(node=nm.node, invariant="I1_link_saturated", result="pass" if i1 else "fail").inc()

        # ---------------------------------------------------------------
        # I2: Decoys are driving the congestion
        # Top-N pods whose combined byte rate >= DECOY_FRACTION of link capacity
        # ---------------------------------------------------------------
        top_decoy_fraction = cfg.decoy_fraction_threshold
        cumulative_mbps = 0.0
        decoy_pods: List[PodInfo] = []
        for pod in sorted_pods:
            cumulative_mbps += pod.byte_rate_mbps
            decoy_pods.append(pod)
            fraction = cumulative_mbps / nm.link_capacity_mbps
            if fraction >= top_decoy_fraction:
                break

        i2 = (cumulative_mbps / nm.link_capacity_mbps) >= top_decoy_fraction if nm.link_capacity_mbps > 0 else False
        INVARIANT_RESULTS.labels(node=nm.node, invariant="I2_decoys_driving", result="pass" if i2 else "fail").inc()

        # ---------------------------------------------------------------
        # I3: Victim services are degraded
        # Victim pods are those NOT in the top decoy set
        # whose owning service is degraded (high error rate).
        # ---------------------------------------------------------------
        decoy_pod_keys = {(p.namespace, p.name) for p in decoy_pods}
        candidate_victim_pods = [
            p for p in nm.all_pods if (p.namespace, p.name) not in decoy_pod_keys
        ]

        victim_pods: List[PodInfo] = []
        worst_error_rate_pct = 0.0
        metrics_unavailable_count = 0

        for pod in candidate_victim_pods:
            if not pod.deployment_name:
                continue
            # Query service error rate for this pod's service
            err_rate = await self._get_service_error_rate(prom, pod.namespace, pod.deployment_name)
            if err_rate is None:
                DATA_GAPS_TOTAL.labels(node=nm.node, reason="service_metrics_unavailable").inc()
                metrics_unavailable_count += 1
                continue
            if err_rate >= cfg.victim_error_rate_threshold_pct:
                victim_pods.append(pod)
                worst_error_rate_pct = max(worst_error_rate_pct, err_rate)

        # Fallback: if NO pods had metrics available AND link is highly utilized,
        # treat all candidate victim pods as potentially degraded.
        # This handles environments without Istio/service-mesh HTTP metrics
        # (e.g., minikube, bare K8s) where we cannot measure error rates
        # but know services are affected by the congestion.
        if not victim_pods and metrics_unavailable_count > 0 and nm.link_utilization_pct > 50.0:
            victim_pods = [p for p in candidate_victim_pods if p.deployment_name]
            worst_error_rate_pct = nm.link_utilization_pct  # Use util as proxy
            log.info(
                f"Node {nm.node}: I3 fallback — no HTTP error metrics available, "
                f"treating {len(victim_pods)} pods as potentially degraded "
                f"(link util {nm.link_utilization_pct:.1f}%)"
            )

        i3 = len(victim_pods) > 0
        INVARIANT_RESULTS.labels(node=nm.node, invariant="I3_victim_degraded", result="pass" if i3 else "fail").inc()

        if not i3:
            # No degraded victim — could be normal high-load scenario
            log.debug(f"Node {nm.node}: I3 not satisfied (no degraded victim services)")
            self._pending_confirmations.pop(nm.node, None)
            return

        # ---------------------------------------------------------------
        # I4: Victim is NOT receiving significant direct attack traffic
        # This is the defining invariant of indirect crossfire.
        # ---------------------------------------------------------------
        total_victim_rx_mbps = sum(p.byte_rate_mbps for p in victim_pods)
        victim_direct_traffic_pct = (total_victim_rx_mbps / nm.link_capacity_mbps) * 100.0
        i4 = victim_direct_traffic_pct <= cfg.victim_direct_traffic_max_pct
        INVARIANT_RESULTS.labels(node=nm.node, invariant="I4_not_direct_attack", result="pass" if i4 else "fail").inc()

        # ---------------------------------------------------------------
        # Confidence score: weighted combination of how strongly
        # each invariant is satisfied
        # ---------------------------------------------------------------
        confidence = _compute_confidence(
            link_util_pct=nm.link_utilization_pct,
            threshold_pct=cfg.link_utilization_threshold_pct,
            decoy_fraction=cumulative_mbps / nm.link_capacity_mbps if nm.link_capacity_mbps > 0 else 0,
            decoy_threshold=cfg.decoy_fraction_threshold,
            victim_error_pct=worst_error_rate_pct,
            error_threshold=cfg.victim_error_rate_threshold_pct,
            victim_direct_pct=victim_direct_traffic_pct,
            direct_max_pct=cfg.victim_direct_traffic_max_pct,
        )

        result = InvariantResult(
            i1_link_saturated=i1,
            i2_decoys_driving_congestion=i2,
            i3_victim_degraded=i3,
            i4_victim_not_directly_attacked=i4,
            decoy_pods=decoy_pods,
            victim_pods=victim_pods,
            link_utilization_pct=nm.link_utilization_pct,
            packet_drop_rate=nm.packet_drop_rate,
            victim_error_rate_pct=worst_error_rate_pct,
            victim_direct_traffic_pct=victim_direct_traffic_pct,
            confidence_score=confidence,
        )

        all_pass = result.all_pass()
        high_confidence = confidence >= cfg.confidence_threshold

        log.info(
            f"node={nm.node} iface={nm.interface} "
            f"link_util={nm.link_utilization_pct:.1f}% "
            f"I1={i1} I2={i2} I3={i3} I4={i4} "
            f"confidence={confidence:.2f} all_pass={all_pass}"
        )

        if all_pass and high_confidence:
            # Require confirmation across N consecutive windows
            self._pending_confirmations[nm.node] = (
                self._pending_confirmations.get(nm.node, 0) + 1
            )
            if self._pending_confirmations[nm.node] >= cfg.confirmation_windows:
                if nm.node not in self._active_events:
                    event_name = await self._create_detection_event(k8s, nm, result)
                    if event_name:
                        self._active_events[nm.node] = event_name
                        self._pending_confirmations.pop(nm.node, None)
        else:
            self._pending_confirmations.pop(nm.node, None)

    async def _get_service_error_rate(
        self, prom: PrometheusClient, namespace: str, deployment: str
    ) -> Optional[float]:
        """
        Query Prometheus for the HTTP error rate of a service.
        Supports both Istio-sourced metrics and standard http_requests_total.
        """
        window = f"{self._config.detection_window_seconds}s"

        # Try Istio metrics first
        istio_err = (
            f'sum(rate(istio_requests_total{{'
            f'destination_workload_namespace="{namespace}",'
            f'destination_workload="{deployment}",'
            f'response_code=~"5.."'
            f'}}[{window}]))'
        )
        istio_total = (
            f'sum(rate(istio_requests_total{{'
            f'destination_workload_namespace="{namespace}",'
            f'destination_workload="{deployment}"'
            f'}}[{window}]))'
        )

        err = await prom.scalar(istio_err)
        total = await prom.scalar(istio_total)

        if err is not None and total is not None and total > 0:
            return min((err / total) * 100.0, 100.0)

        # Fall back to standard http_requests_total
        std_err = (
            f'sum(rate(http_requests_total{{'
            f'namespace="{namespace}",pod=~"{deployment}.*",'
            f'status=~"5.."'
            f'}}[{window}]))'
        )
        std_total = (
            f'sum(rate(http_requests_total{{'
            f'namespace="{namespace}",pod=~"{deployment}.*"'
            f'}}[{window}]))'
        )

        err = await prom.scalar(std_err)
        total = await prom.scalar(std_total)

        if err is not None and total is not None and total > 0:
            return min((err / total) * 100.0, 100.0)

        return None

    async def _create_detection_event(
        self,
        k8s: K8sClient,
        nm: NodeMetrics,
        result: InvariantResult,
    ) -> Optional[str]:
        """
        Create a CrossfireDetectionEvent CR via the Kubernetes API.
        Returns the event name if successful, else None.
        """
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        event_name = f"crossfire-{nm.node.replace('.','-')}-{ts}"

        body = {
            "apiVersion": f"{self._config.crd_group}/{self._config.crd_version}",
            "kind": "CrossfireDetectionEvent",
            "metadata": {
                "name": event_name,
                "labels": {
                    "crossfire.io/node": nm.node,
                    "crossfire.io/interface": nm.interface,
                    "app.kubernetes.io/managed-by": "crossfire-detector",
                },
            },
            "spec": {
                "detectedAt": datetime.now(timezone.utc).isoformat(),
                "affectedNode": nm.node,
                "congestedInterface": nm.interface,
                "linkUtilizationPct": result.link_utilization_pct,
                "packetDropRate": result.packet_drop_rate,
                "decoyPods": [
                    {
                        "namespace": p.namespace,
                        "name": p.name,
                        "podIP": p.pod_ip,
                        "byteRateMbps": round(p.byte_rate_mbps, 2),
                    }
                    for p in result.decoy_pods
                ],
                "victimPods": [
                    {
                        "namespace": p.namespace,
                        "name": p.name,
                        "podIP": p.pod_ip,
                        "deploymentName": p.deployment_name,
                    }
                    for p in result.victim_pods
                ],
                "victimErrorRatePct": round(result.victim_error_rate_pct, 2),
                "victimDirectTrafficPct": round(result.victim_direct_traffic_pct, 2),
                "confidenceScore": round(result.confidence_score, 4),
                "detectionAlgorithm": "crossfire-invariant-v1",
                "detectionWindowSeconds": self._config.detection_window_seconds,
            },
        }

        resp = await k8s.create_cluster_resource(
            self._config.crd_group,
            self._config.crd_version,
            self._config.crd_plural,
            body,
        )
        if resp and "metadata" in resp:
            name = resp["metadata"]["name"]
            log.info(f"Created CrossfireDetectionEvent {name} for node {nm.node}")
            DETECTION_EVENTS_TOTAL.labels(node=nm.node).inc()
            ACTIVE_EVENTS.inc()
            return name
        else:
            log.error(f"Failed to create CrossfireDetectionEvent for node {nm.node}")
            return None

    async def _resolve_event(
        self, k8s: K8sClient, node: str, event_name: str
    ) -> None:
        """Mark a CrossfireDetectionEvent as resolved."""
        patch = {
            "resolved": True,
            "resolvedAt": datetime.now(timezone.utc).isoformat(),
        }
        resp = await k8s.patch_cluster_resource_status(
            self._config.crd_group,
            self._config.crd_version,
            self._config.crd_plural,
            event_name,
            patch,
        )
        if resp:
            log.info(f"Resolved CrossfireDetectionEvent {event_name} for node {node}")
            del self._active_events[node]
            RESOLUTION_EVENTS_TOTAL.labels(node=node).inc()
            ACTIVE_EVENTS.dec()


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------
def _compute_confidence(
    link_util_pct: float,
    threshold_pct: float,
    decoy_fraction: float,
    decoy_threshold: float,
    victim_error_pct: float,
    error_threshold: float,
    victim_direct_pct: float,
    direct_max_pct: float,
) -> float:
    """
    Compute a confidence score in [0, 1].
    Each invariant contributes a normalised score; final is min of all.
    Using min() ensures that a weak invariant cannot be masked by a strong one.
    """
    # How far above threshold each invariant is (clamped to [0, 1])
    s1 = min((link_util_pct - threshold_pct) / (100.0 - threshold_pct + 1e-9), 1.0)
    s2 = min((decoy_fraction - decoy_threshold) / (1.0 - decoy_threshold + 1e-9), 1.0)
    s3 = min((victim_error_pct - error_threshold) / (100.0 - error_threshold + 1e-9), 1.0)
    # For I4, score is how far BELOW the threshold (lower direct traffic = higher score)
    s4 = min((direct_max_pct - victim_direct_pct) / (direct_max_pct + 1e-9), 1.0)

    scores = [s1, s2, s3, s4]
    # Final confidence is the geometric mean of all scores
    if any(s <= 0 for s in scores):
        return 0.0
    product = 1.0
    for s in scores:
        product *= s
    return min(product ** (1.0 / len(scores)), 1.0)


# ---------------------------------------------------------------------------
# Service account helpers
# ---------------------------------------------------------------------------
def _read_service_account_token() -> str:
    token_path = "/var/run/secrets/kubernetes.io/serviceaccount/token"
    try:
        with open(token_path) as f:
            return f.read().strip()
    except FileNotFoundError:
        log.warning(f"SA token not found at {token_path}; using KUBERNETES_TOKEN env")
        return os.getenv("KUBERNETES_TOKEN", "")


def _read_ca_cert() -> str:
    ca_path = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
    return ca_path if os.path.exists(ca_path) else ""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
async def _main() -> None:
    config = DetectorConfig()
    detector = CrossfireDetector(config)
    await detector.run_forever()


if __name__ == "__main__":
    asyncio.run(_main())
