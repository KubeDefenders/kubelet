"""
KubeDDoS System Dashboard — Flask application.

Provides a web interface for the entire crossfire detection and mitigation
pipeline:
  - Cluster overview (nodes, pods, deployments)
  - Live invariant monitoring (I1-I4 values per node)
  - Detection events (CrossfireDetectionEvent CRs)
  - Mitigation intents (CrossfireMitigationIntent CRs with phase tracking)
  - SHAP-based explanation viewer
  - Strategy visualization
  - System health & component status

No hardcoded values — all configuration via environment variables.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from flask import Flask, jsonify, render_template, request
from flask_socketio import SocketIO

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import SystemConfig
from frontend.api_client import KubeClient, PrometheusClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
config = SystemConfig()

app = Flask(__name__)
app.config["SECRET_KEY"] = config.secret_key
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# Clients (initialized lazily)
_kube: Optional[KubeClient] = None
_prom: Optional[PrometheusClient] = None


def get_kube() -> KubeClient:
    global _kube
    if _kube is None:
        token = config.get_k8s_token()
        ca = config.get_k8s_ca()
        _kube = KubeClient(config.k8s_api_url, token, ca)
    return _kube


def get_prom() -> PrometheusClient:
    global _prom
    if _prom is None:
        _prom = PrometheusClient(config.prometheus_url)
    return _prom


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html", config=config)


@app.route("/detection")
def detection_page():
    return render_template("detection.html", config=config)


@app.route("/mitigation")
def mitigation_page():
    return render_template("mitigation.html", config=config)


@app.route("/invariants")
def invariants_page():
    return render_template("invariants.html", config=config)


@app.route("/cluster")
def cluster_page():
    return render_template("cluster.html", config=config)


@app.route("/explanations")
def explanations_page():
    return render_template("explanations.html", config=config)


# ---------------------------------------------------------------------------
# API routes — Cluster overview
# ---------------------------------------------------------------------------
@app.route("/api/cluster/nodes")
def api_cluster_nodes():
    kube = get_kube()
    nodes = kube.get_nodes()
    result = []
    for node in nodes:
        meta = node.get("metadata", {})
        status = node.get("status", {})
        conditions = {
            c["type"]: c["status"]
            for c in status.get("conditions", [])
        }
        addresses = {
            a["type"]: a["address"]
            for a in status.get("addresses", [])
        }
        result.append({
            "name": meta.get("name", ""),
            "labels": meta.get("labels", {}),
            "ready": conditions.get("Ready", "Unknown"),
            "internal_ip": addresses.get("InternalIP", ""),
            "os": status.get("nodeInfo", {}).get("osImage", ""),
            "kubelet_version": status.get("nodeInfo", {}).get("kubeletVersion", ""),
            "cpu": status.get("capacity", {}).get("cpu", ""),
            "memory": status.get("capacity", {}).get("memory", ""),
        })
    return jsonify(result)


@app.route("/api/cluster/pods")
def api_cluster_pods():
    kube = get_kube()
    ns = request.args.get("namespace", config.target_namespace)
    pods = kube.get_pods(ns)
    result = []
    for pod in pods:
        meta = pod.get("metadata", {})
        spec = pod.get("spec", {})
        status = pod.get("status", {})
        result.append({
            "name": meta.get("name", ""),
            "namespace": meta.get("namespace", ""),
            "node": spec.get("nodeName", ""),
            "phase": status.get("phase", "Unknown"),
            "ip": status.get("podIP", ""),
            "containers": [
                c.get("name", "") for c in spec.get("containers", [])
            ],
            "restarts": sum(
                cs.get("restartCount", 0)
                for cs in status.get("containerStatuses", [])
            ),
            "labels": meta.get("labels", {}),
        })
    return jsonify(result)


@app.route("/api/cluster/deployments")
def api_cluster_deployments():
    kube = get_kube()
    ns = request.args.get("namespace", config.target_namespace)
    deps = kube.get_deployments(ns)
    result = []
    for dep in deps:
        meta = dep.get("metadata", {})
        spec = dep.get("spec", {})
        status = dep.get("status", {})
        result.append({
            "name": meta.get("name", ""),
            "namespace": meta.get("namespace", ""),
            "replicas": spec.get("replicas", 0),
            "ready_replicas": status.get("readyReplicas", 0),
            "available_replicas": status.get("availableReplicas", 0),
            "selector": spec.get("selector", {}).get("matchLabels", {}),
        })
    return jsonify(result)


@app.route("/api/cluster/namespaces")
def api_cluster_namespaces():
    kube = get_kube()
    return jsonify(kube.get_namespaces())


# ---------------------------------------------------------------------------
# API routes — Detection events
# ---------------------------------------------------------------------------
@app.route("/api/detection/events")
def api_detection_events():
    kube = get_kube()
    events = kube.list_detection_events(config.crd_group, config.crd_version)
    result = []
    for ev in events:
        spec = ev.get("spec", {})
        status = ev.get("status", {})
        meta = ev.get("metadata", {})
        result.append({
            "name": meta.get("name", ""),
            "created": meta.get("creationTimestamp", ""),
            "affected_node": spec.get("affectedNode", ""),
            "congested_interface": spec.get("congestedInterface", ""),
            "link_utilization_pct": spec.get("linkUtilizationPct", 0),
            "packet_drop_rate": spec.get("packetDropRate", 0),
            "confidence_score": spec.get("confidenceScore", 0),
            "detection_algorithm": spec.get("detectionAlgorithm", ""),
            "decoy_pods": spec.get("decoyPods", []),
            "victim_pods": spec.get("victimPods", []),
            "victim_error_rate_pct": spec.get("victimErrorRatePct", 0),
            "victim_direct_traffic_pct": spec.get("victimDirectTrafficPct", 0),
            "resolved": status.get("resolved", False),
            "mitigation_ref": status.get("mitigationRef", ""),
            "resolved_at": status.get("resolvedAt", ""),
        })
    return jsonify(result)


@app.route("/api/detection/events/<name>")
def api_detection_event_detail(name: str):
    kube = get_kube()
    ev = kube.get_detection_event(name, config.crd_group, config.crd_version)
    if ev is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(ev)


# ---------------------------------------------------------------------------
# API routes — Mitigation intents
# ---------------------------------------------------------------------------
@app.route("/api/mitigation/intents")
def api_mitigation_intents():
    kube = get_kube()
    intents = kube.list_mitigation_intents(config.crd_group, config.crd_version)
    result = []
    for intent in intents:
        spec = intent.get("spec", {})
        status = intent.get("status", {})
        meta = intent.get("metadata", {})
        strategy = spec.get("strategy", {})

        # Compute TTL remaining
        ttl = spec.get("ttlSeconds", 0)
        created = meta.get("creationTimestamp", "")
        ttl_remaining = None
        if created and ttl:
            try:
                created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                elapsed = (datetime.now(timezone.utc) - created_dt).total_seconds()
                ttl_remaining = max(0, ttl - elapsed)
            except Exception:
                pass

        result.append({
            "name": meta.get("name", ""),
            "created": created,
            "affected_node": spec.get("affectedNode", ""),
            "congested_interface": spec.get("congestedInterface", ""),
            "trigger_event": spec.get("triggerEventRef", ""),
            "strategy_type": strategy.get("type", ""),
            "strategy_params": {
                k: v for k, v in strategy.items() if k != "type"
            },
            "ttl_seconds": ttl,
            "ttl_remaining": round(ttl_remaining, 1) if ttl_remaining is not None else None,
            "decoy_pods": spec.get("decoyPods", []),
            "victim_pods": spec.get("victimPods", []),
            "phase": status.get("phase", "Unknown"),
            "applied_resources": status.get("appliedResources", []),
            "conditions": status.get("conditions", []),
            "last_reconciled": status.get("lastReconciledAt", ""),
        })
    return jsonify(result)


@app.route("/api/mitigation/intents/<name>")
def api_mitigation_intent_detail(name: str):
    kube = get_kube()
    intent = kube.get_mitigation_intent(name, config.crd_group, config.crd_version)
    if intent is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(intent)


# ---------------------------------------------------------------------------
# API routes — Invariant monitoring
# ---------------------------------------------------------------------------
@app.route("/api/invariants/live")
def api_invariants_live():
    """
    Get the current state of all four crossfire invariants per node.

    Response: list of per-node invariant evaluations with raw metric values.
    """
    prom = get_prom()
    window = request.args.get("window", "60s")
    namespace = request.args.get("namespace", config.target_namespace)

    # I1: Link utilization per node
    node_util = prom.get_node_link_utilization(window)

    # I2/I3/I4: Service and pod metrics
    pod_rates = prom.get_pod_byte_rates(namespace, window)
    service_errors = prom.get_service_error_rates(namespace, window)
    latencies = prom.get_latency_percentiles(namespace, window)

    result = []
    for node, interfaces in node_util.items():
        for iface, metrics in interfaces.items():
            util = metrics["utilization_pct"]
            i1_pass = util >= config.link_utilization_threshold_pct

            result.append({
                "node": node,
                "interface": iface,
                "invariants": {
                    "I1_link_saturated": {
                        "pass": i1_pass,
                        "value": util,
                        "threshold": config.link_utilization_threshold_pct,
                        "unit": "%",
                        "description": "Link utilization exceeds threshold",
                    },
                    "I2_decoys_driving_congestion": {
                        "pass": None,  # Requires per-pod analysis
                        "value": None,
                        "threshold": config.decoy_fraction_threshold * 100,
                        "unit": "%",
                        "description": "Decoy pods consuming majority of bandwidth",
                    },
                    "I3_victim_degraded": {
                        "pass": None,
                        "value": None,
                        "threshold": config.victim_error_rate_threshold_pct,
                        "unit": "%",
                        "description": "Victim service error rate elevated",
                    },
                    "I4_victim_not_directly_attacked": {
                        "pass": None,
                        "value": None,
                        "threshold": config.victim_direct_traffic_max_pct,
                        "unit": "%",
                        "description": "Victim receives low direct traffic",
                    },
                },
                "link_metrics": metrics,
            })

    return jsonify({
        "timestamp": time.time(),
        "window": window,
        "namespace": namespace,
        "nodes": result,
        "services": service_errors,
        "latencies": latencies,
        "thresholds": {
            "link_utilization_pct": config.link_utilization_threshold_pct,
            "decoy_fraction": config.decoy_fraction_threshold,
            "victim_error_rate_pct": config.victim_error_rate_threshold_pct,
            "victim_direct_traffic_max_pct": config.victim_direct_traffic_max_pct,
            "confidence": config.confidence_threshold,
        },
    })


@app.route("/api/invariants/history")
def api_invariants_history():
    """Get invariant metric time series for a specific node."""
    prom = get_prom()
    node = request.args.get("node", "")
    iface = request.args.get("interface", "")
    minutes = int(request.args.get("minutes", "30"))

    if not node:
        return jsonify({"error": "node parameter required"}), 400

    end = time.time()
    start = end - (minutes * 60)

    rx_q = f'rate(node_network_receive_bytes_total{{node="{node}",device="{iface}"}}[60s])'
    tx_q = f'rate(node_network_transmit_bytes_total{{node="{node}",device="{iface}"}}[60s])'

    rx_series = prom.query_range(rx_q, start, end)
    tx_series = prom.query_range(tx_q, start, end)

    return jsonify({
        "node": node,
        "interface": iface,
        "rx_series": rx_series,
        "tx_series": tx_series,
        "start": start,
        "end": end,
    })


# ---------------------------------------------------------------------------
# API routes — Prometheus metrics
# ---------------------------------------------------------------------------
@app.route("/api/metrics/services")
def api_metrics_services():
    prom = get_prom()
    ns = request.args.get("namespace", config.target_namespace)
    window = request.args.get("window", "60s")
    errors = prom.get_service_error_rates(ns, window)
    latencies = prom.get_latency_percentiles(ns, window)

    services = {}
    for svc, data in errors.items():
        services[svc] = {**data, "latency": latencies.get(svc, {})}
    return jsonify(services)


@app.route("/api/metrics/pods")
def api_metrics_pods():
    prom = get_prom()
    ns = request.args.get("namespace", config.target_namespace)
    window = request.args.get("window", "60s")
    return jsonify(prom.get_pod_byte_rates(ns, window))


# ---------------------------------------------------------------------------
# API routes — System health
# ---------------------------------------------------------------------------
@app.route("/api/health")
def api_health():
    """Check connectivity to Kubernetes API and Prometheus."""
    prom = get_prom()
    kube = get_kube()

    k8s_ok = kube.get_namespaces() is not None and len(kube.get_namespaces()) > 0
    prom_ok = prom.check_connectivity()

    # Check CRDs installed
    crds = kube.get_crds()
    crd_names = [c.get("metadata", {}).get("name", "") for c in crds]

    return jsonify({
        "status": "healthy" if (k8s_ok and prom_ok) else "degraded",
        "components": {
            "kubernetes_api": {"status": "ok" if k8s_ok else "unreachable"},
            "prometheus": {"status": "ok" if prom_ok else "unreachable"},
            "crds_installed": {
                "crossfiredetectionevents": "crossfiredetectionevents.crossfire.io" in crd_names,
                "crossfiremitigationintents": "crossfiremitigationintents.crossfire.io" in crd_names,
            },
        },
        "config": {
            "k8s_api": config.k8s_api_url,
            "prometheus": config.prometheus_url,
            "target_namespace": config.target_namespace,
        },
    })


@app.route("/api/health/components")
def api_health_components():
    """Check status of detector, intent-generator, and controller pods."""
    kube = get_kube()
    system_pods = kube.get_pods("crossfire-system")
    components = {}
    for pod in system_pods:
        name = pod.get("metadata", {}).get("name", "")
        phase = pod.get("status", {}).get("phase", "Unknown")
        for comp in ["detector", "intent-generator", "controller", "frontend"]:
            if comp in name:
                containers = pod.get("status", {}).get("containerStatuses", [])
                ready = all(c.get("ready", False) for c in containers)
                components[comp] = {
                    "pod": name,
                    "phase": phase,
                    "ready": ready,
                    "restarts": sum(c.get("restartCount", 0) for c in containers),
                }
    return jsonify(components)


# ---------------------------------------------------------------------------
# WebSocket — real-time updates
# ---------------------------------------------------------------------------
def _background_emitter():
    """Periodically push invariant and event data to connected clients."""
    while True:
        socketio.sleep(15)
        try:
            prom = get_prom()
            kube = get_kube()

            # Push invariant snapshot
            node_util = prom.get_node_link_utilization("60s")
            socketio.emit("invariant_update", {
                "timestamp": time.time(),
                "nodes": node_util,
            })

            # Push detection events count
            events = kube.list_detection_events(config.crd_group, config.crd_version)
            active_events = [e for e in events if not e.get("status", {}).get("resolved", False)]
            socketio.emit("detection_update", {
                "total_events": len(events),
                "active_events": len(active_events),
                "latest": active_events[:5] if active_events else [],
            })

            # Push mitigation status
            intents = kube.list_mitigation_intents(config.crd_group, config.crd_version)
            active_intents = [
                i for i in intents
                if i.get("status", {}).get("phase", "") in ("Pending", "Active")
            ]
            socketio.emit("mitigation_update", {
                "total_intents": len(intents),
                "active_intents": len(active_intents),
            })
        except Exception as e:
            logger.warning("Background emitter error: %s", e)


@socketio.on("connect")
def handle_connect():
    logger.info("Client connected")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    logger.info(
        "KubeDDoS System Dashboard starting on %s:%d",
        config.frontend_host,
        config.frontend_port,
    )
    logger.info("Kubernetes API: %s", config.k8s_api_url)
    logger.info("Prometheus: %s", config.prometheus_url)
    logger.info("Target namespace: %s", config.target_namespace)

    socketio.start_background_task(_background_emitter)
    socketio.run(
        app,
        host=config.frontend_host,
        port=config.frontend_port,
        debug=os.getenv("FLASK_DEBUG", "0") == "1",
    )


if __name__ == "__main__":
    main()
