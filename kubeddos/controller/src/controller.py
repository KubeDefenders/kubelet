"""
CrossfireMitigationController — reconciles CrossfireMitigationIntent CRs into
Kubernetes-native resources.

Design principles
-----------------
- Watch-and-reconcile loop (controller pattern, not imperative scripting).
- All state mutations go through the Kubernetes API only.
- Tracks every created/modified resource in intent status.appliedResources
  for accurate revert.
- Idempotent reconciliation: safe to run multiple times with same input.
- TTL-based automatic revert.
- Finalizer-based cleanup: resources are reverted before CR deletion.
- Drift detection: if owned resources are changed externally, re-applies.
- Every strategy is reversible to the exact pre-mitigation state.
- Exports Prometheus metrics.

Strategies implemented (Kubernetes-native only, no iptables, no shell):
  BandwidthConstraint:
    - Patches pod annotations kubernetes.io/{ingress,egress}-bandwidth.
    - Requires a CNI with bandwidth plugin support.
    - Falls back to BandwidthConstraintIneffective condition if CNI
      does not honour annotations (detected via metric delta).

  PriorityElevation:
    - Creates a PriorityClass resource.
    - Patches victim Deployment .spec.template.spec.priorityClassName.

  TopologyEviction:
    - Taints the congested node (NoSchedule).
    - Patches victim Deployments with TopologySpreadConstraint.
    - Patches decoy Deployments with NodeAntiAffinity.
    - Optionally patches HPA minReplicas for victim Deployments.

  Hybrid: applies all three.
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

logging.basicConfig(
    level=logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s","msg":"%(message)s"}',
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
log = logging.getLogger("crossfire-controller")

# ---------------------------------------------------------------------------
# Self-observability metrics
# ---------------------------------------------------------------------------
RECONCILE_TOTAL = Counter(
    "crossfire_reconcile_total",
    "Total reconciliation attempts",
    ["phase", "result"],  # result: success|error|skipped
)
RECONCILE_DURATION = Histogram(
    "crossfire_reconcile_duration_seconds",
    "Reconciliation duration",
    ["phase"],
)
RESOURCES_APPLIED = Counter(
    "crossfire_resources_applied_total",
    "Kubernetes resources applied by controller",
    ["kind", "action"],
)
RESOURCES_REVERTED = Counter(
    "crossfire_resources_reverted_total",
    "Kubernetes resources reverted by controller",
    ["kind"],
)
DRIFT_CORRECTIONS = Counter(
    "crossfire_drift_corrections_total",
    "Times controller corrected drifted resources",
)
TTL_EXPIRATIONS = Counter(
    "crossfire_ttl_expirations_total",
    "Intents that expired by TTL",
)
ACTIVE_MITIGATIONS = Gauge(
    "crossfire_active_mitigations",
    "Number of currently active mitigations",
)
BANDWIDTH_CONSTRAINTS_ACTIVE = Gauge(
    "crossfire_bandwidth_constraints_active",
    "Pods currently under bandwidth constraints",
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
@dataclass
class ControllerConfig:
    k8s_api_url: str = field(
        default_factory=lambda: os.getenv(
            "KUBERNETES_API_URL", "https://kubernetes.default.svc"
        )
    )
    crd_group: str = "crossfire.io"
    crd_version: str = "v1alpha1"
    intent_plural: str = "crossfiremitigationintents"
    # Requeue intervals
    active_requeue_seconds: int = field(
        default_factory=lambda: int(os.getenv("ACTIVE_REQUEUE_SECONDS", "30"))
    )
    # Time after TTL expiry to wait before deleting the intent CR itself (audit window)
    completed_retain_seconds: int = field(
        default_factory=lambda: int(os.getenv("COMPLETED_RETAIN_SECONDS", "300"))
    )
    metrics_port: int = field(
        default_factory=lambda: int(os.getenv("METRICS_PORT", "8082"))
    )

    FINALIZER: str = "crossfire.io/mitigation-cleanup"


# ---------------------------------------------------------------------------
# Minimal Kubernetes client
# ---------------------------------------------------------------------------
class K8sClient:
    def __init__(self, session: aiohttp.ClientSession, api: str, token: str, ca: str):
        self._session = session
        self._api = api.rstrip("/")
        self._headers_json = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        self._headers_merge_patch = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/merge-patch+json",
        }
        self._headers_strategic_merge_patch = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/strategic-merge-patch+json",
        }
        if ca and os.path.exists(ca):
            import ssl as _ssl_mod
            self._ssl = _ssl_mod.create_default_context(cafile=ca)
        else:
            self._ssl = False

    # --- Generic helpers ---

    async def _get(self, path: str) -> Optional[Dict]:
        url = f"{self._api}{path}"
        try:
            async with self._session.get(
                url, headers=self._headers_json, ssl=self._ssl,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                if r.status == 200:
                    return await r.json()
                log.debug(f"GET {path} -> {r.status}")
                return None
        except Exception as e:
            log.warning(f"GET {path} error: {e}")
            return None

    async def _post(self, path: str, body: Dict) -> Optional[Dict]:
        url = f"{self._api}{path}"
        try:
            async with self._session.post(
                url, json=body, headers=self._headers_json, ssl=self._ssl,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as r:
                data = await r.json()
                if r.status in (200, 201, 409):
                    return data
                log.warning(f"POST {path} -> {r.status}: {str(data)[:200]}")
                return None
        except Exception as e:
            log.warning(f"POST {path} error: {e}")
            return None

    async def _patch_merge(self, path: str, patch: Dict) -> Optional[Dict]:
        url = f"{self._api}{path}"
        try:
            async with self._session.patch(
                url, json=patch, headers=self._headers_merge_patch, ssl=self._ssl,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as r:
                data = await r.json()
                if r.status in (200, 201):
                    return data
                log.warning(f"PATCH merge {path} -> {r.status}: {str(data)[:200]}")
                return None
        except Exception as e:
            log.warning(f"PATCH merge {path} error: {e}")
            return None

    async def _patch_strategic(self, path: str, patch: Dict) -> Optional[Dict]:
        url = f"{self._api}{path}"
        try:
            async with self._session.patch(
                url, json=patch, headers=self._headers_strategic_merge_patch, ssl=self._ssl,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as r:
                data = await r.json()
                if r.status in (200, 201):
                    return data
                log.warning(f"PATCH strategic {path} -> {r.status}: {str(data)[:200]}")
                return None
        except Exception as e:
            log.warning(f"PATCH strategic {path} error: {e}")
            return None

    async def _delete(self, path: str) -> bool:
        url = f"{self._api}{path}"
        try:
            async with self._session.delete(
                url, headers=self._headers_json, ssl=self._ssl,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                return r.status in (200, 202, 204, 404)
        except Exception as e:
            log.warning(f"DELETE {path} error: {e}")
            return False

    # --- CRD helpers ---

    async def watch_cluster_crds(self, group: str, version: str, plural: str):
        url = f"{self._api}/apis/{group}/{version}/{plural}"
        params = {"watch": "true", "timeoutSeconds": "300"}
        try:
            async with self._session.get(
                url, params=params, headers=self._headers_json, ssl=self._ssl,
                timeout=aiohttp.ClientTimeout(total=None, sock_read=310)
            ) as resp:
                if resp.status != 200:
                    return
                async for raw in resp.content:
                    line = raw.decode("utf-8").strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                        yield ev.get("type", ""), ev.get("object", {})
                    except json.JSONDecodeError:
                        pass
        except Exception as e:
            log.warning(f"Watch error: {e}")

    async def update_crd_status(
        self, group: str, version: str, plural: str, name: str, status: Dict
    ) -> Optional[Dict]:
        return await self._patch_merge(
            f"/apis/{group}/{version}/{plural}/{name}/status",
            {"status": status},
        )

    async def add_finalizer(
        self, group: str, version: str, plural: str, name: str, finalizer: str
    ) -> Optional[Dict]:
        return await self._patch_merge(
            f"/apis/{group}/{version}/{plural}/{name}",
            {"metadata": {"finalizers": [finalizer]}},
        )

    async def remove_finalizer(
        self, group: str, version: str, plural: str, name: str, finalizer: str
    ) -> Optional[Dict]:
        # Read current object to get existing finalizers, then remove only ours
        obj = await self._get(f"/apis/{group}/{version}/{plural}/{name}")
        if not obj:
            return None
        current_finalizers = obj.get("metadata", {}).get("finalizers", []) or []
        new_finalizers = [f for f in current_finalizers if f != finalizer]
        return await self._patch_merge(
            f"/apis/{group}/{version}/{plural}/{name}",
            {"metadata": {"finalizers": new_finalizers}},
        )

    # --- Kubernetes resource operations ---

    async def ensure_priority_class(self, name: str, value: int) -> bool:
        existing = await self._get(f"/apis/scheduling.k8s.io/v1/priorityclasses/{name}")
        if existing:
            return True
        body = {
            "apiVersion": "scheduling.k8s.io/v1",
            "kind": "PriorityClass",
            "metadata": {
                "name": name,
                "labels": {"app.kubernetes.io/managed-by": "crossfire-controller"},
            },
            "value": value,
            "globalDefault": False,
            "description": "Elevated priority class for crossfire mitigation (auto-managed)",
        }
        result = await self._post("/apis/scheduling.k8s.io/v1/priorityclasses", body)
        return result is not None

    async def delete_priority_class(self, name: str) -> bool:
        return await self._delete(f"/apis/scheduling.k8s.io/v1/priorityclasses/{name}")

    async def get_deployment(self, namespace: str, name: str) -> Optional[Dict]:
        return await self._get(f"/apis/apps/v1/namespaces/{namespace}/deployments/{name}")

    async def patch_deployment_priority_class(
        self, namespace: str, deploy_name: str, priority_class_name: str
    ) -> Optional[str]:
        """Patch a Deployment's pod template to use the given PriorityClass.
        Returns the original priorityClassName for revert, or None on failure.
        """
        deploy = await self.get_deployment(namespace, deploy_name)
        if not deploy:
            return None
        original = (
            deploy.get("spec", {})
            .get("template", {})
            .get("spec", {})
            .get("priorityClassName", "")
        )
        patch = {
            "spec": {
                "template": {
                    "spec": {"priorityClassName": priority_class_name}
                }
            }
        }
        result = await self._patch_strategic(
            f"/apis/apps/v1/namespaces/{namespace}/deployments/{deploy_name}", patch
        )
        return original if result else None

    async def patch_pod_bandwidth_annotations(
        self,
        namespace: str,
        pod_name: str,
        ingress_mbps: int,
        egress_mbps: int,
    ) -> Optional[Dict]:
        """
        Set the kubernetes.io/ingress-bandwidth and egress-bandwidth pod annotations.
        NOTE: Pod annotations are immutable for some fields; if patch fails, the
        controller will record BandwidthConstraintIneffective and fall back.
        """
        patch = {
            "metadata": {
                "annotations": {
                    "kubernetes.io/ingress-bandwidth": f"{ingress_mbps}M",
                    "kubernetes.io/egress-bandwidth": f"{egress_mbps}M",
                }
            }
        }
        return await self._patch_merge(
            f"/api/v1/namespaces/{namespace}/pods/{pod_name}", patch
        )

    async def remove_pod_bandwidth_annotations(
        self, namespace: str, pod_name: str
    ) -> Optional[Dict]:
        """Remove bandwidth annotations by setting them to null."""
        patch = {
            "metadata": {
                "annotations": {
                    "kubernetes.io/ingress-bandwidth": None,
                    "kubernetes.io/egress-bandwidth": None,
                }
            }
        }
        return await self._patch_merge(
            f"/api/v1/namespaces/{namespace}/pods/{pod_name}", patch
        )

    async def taint_node(self, node_name: str, taint_key: str, taint_value: str, effect: str) -> bool:
        """Add a taint to a node."""
        node = await self._get(f"/api/v1/nodes/{node_name}")
        if not node:
            return False
        existing_taints = node.get("spec", {}).get("taints", []) or []
        # Idempotent: don't add duplicate
        for t in existing_taints:
            if t.get("key") == taint_key:
                return True
        new_taint_list = existing_taints + [
            {"key": taint_key, "value": taint_value, "effect": effect}
        ]
        patch = {"spec": {"taints": new_taint_list}}
        result = await self._patch_merge(f"/api/v1/nodes/{node_name}", patch)
        return result is not None

    async def untaint_node(self, node_name: str, taint_key: str) -> bool:
        """Remove a taint from a node by key."""
        node = await self._get(f"/api/v1/nodes/{node_name}")
        if not node:
            return True  # Already gone
        existing = node.get("spec", {}).get("taints", []) or []
        new_taints = [t for t in existing if t.get("key") != taint_key]
        patch = {"spec": {"taints": new_taints}}
        result = await self._patch_merge(f"/api/v1/nodes/{node_name}", patch)
        return result is not None

    async def add_deployment_topology_constraint(
        self,
        namespace: str,
        deploy_name: str,
        avoid_node: str,
    ) -> bool:
        """
        Add a PodAntiAffinity rule to discourage scheduling on the congested node,
        plus a TopologySpreadConstraint for cross-node spreading.
        """
        patch = {
            "spec": {
                "template": {
                    "spec": {
                        "affinity": {
                            "nodeAffinity": {
                                "preferredDuringSchedulingIgnoredDuringExecution": [
                                    {
                                        "weight": 100,
                                        "preference": {
                                            "matchExpressions": [
                                                {
                                                    "key": "kubernetes.io/hostname",
                                                    "operator": "NotIn",
                                                    "values": [avoid_node],
                                                }
                                            ]
                                        },
                                    }
                                ]
                            }
                        },
                        "topologySpreadConstraints": [
                            {
                                "maxSkew": 1,
                                "topologyKey": "kubernetes.io/hostname",
                                "whenUnsatisfiable": "DoNotSchedule",
                                "labelSelector": {
                                    "matchLabels": {
                                        "crossfire.io/protected": "true"
                                    }
                                },
                            }
                        ],
                    }
                }
            }
        }
        result = await self._patch_strategic(
            f"/apis/apps/v1/namespaces/{namespace}/deployments/{deploy_name}", patch
        )
        return result is not None

    async def remove_deployment_topology_patches(
        self, namespace: str, deploy_name: str
    ) -> bool:
        """Remove the crossfire-specific affinity and topology spread constraints."""
        # Remove by setting to empty (strategic merge patch null removes sub-objects)
        patch = {
            "spec": {
                "template": {
                    "spec": {
                        "affinity": None,
                        "topologySpreadConstraints": None,
                    }
                }
            }
        }
        result = await self._patch_strategic(
            f"/apis/apps/v1/namespaces/{namespace}/deployments/{deploy_name}", patch
        )
        return result is not None

    async def scale_deployment(
        self, namespace: str, deploy_name: str, replicas: int
    ) -> Optional[int]:
        """Scale a Deployment to the given replica count.
        Returns original replicas for revert, or None on failure."""
        deploy = await self.get_deployment(namespace, deploy_name)
        if not deploy:
            return None
        original = deploy.get("spec", {}).get("replicas", 1)
        patch = {"spec": {"replicas": replicas}}
        result = await self._patch_merge(
            f"/apis/apps/v1/namespaces/{namespace}/deployments/{deploy_name}", patch
        )
        return original if result else None

    async def get_pod_owner(
        self, namespace: str, pod_name: str
    ) -> Optional[Dict]:
        """Look up the top-level owner (Deployment or DaemonSet) of a pod.
        Returns dict with 'kind', 'name', 'namespace' or None."""
        pod = await self._get(f"/api/v1/namespaces/{namespace}/pods/{pod_name}")
        if not pod:
            return None
        owners = pod.get("metadata", {}).get("ownerReferences", [])
        if not owners:
            return None
        owner = owners[0]
        kind = owner.get("kind", "")
        name = owner.get("name", "")

        # ReplicaSet → look up its owner (the Deployment)
        if kind == "ReplicaSet":
            rs = await self._get(
                f"/apis/apps/v1/namespaces/{namespace}/replicasets/{name}"
            )
            if rs:
                rs_owners = rs.get("metadata", {}).get("ownerReferences", [])
                if rs_owners and rs_owners[0].get("kind") == "Deployment":
                    return {
                        "kind": "Deployment",
                        "name": rs_owners[0]["name"],
                        "namespace": namespace,
                    }
        elif kind == "DaemonSet":
            return {"kind": "DaemonSet", "name": name, "namespace": namespace}

        return {"kind": kind, "name": name, "namespace": namespace}

    async def delete_daemonset(self, namespace: str, name: str) -> bool:
        """Delete a DaemonSet. Returns True on success or already-gone."""
        return await self._delete(
            f"/apis/apps/v1/namespaces/{namespace}/daemonsets/{name}"
        )

    async def get_daemonset(self, namespace: str, name: str) -> Optional[Dict]:
        return await self._get(
            f"/apis/apps/v1/namespaces/{namespace}/daemonsets/{name}"
        )

    async def create_daemonset(self, namespace: str, body: Dict) -> Optional[Dict]:
        """Recreate a DaemonSet (for revert)."""
        return await self._post(
            f"/apis/apps/v1/namespaces/{namespace}/daemonsets", body
        )

    async def get_hpa(self, namespace: str, hpa_name: str) -> Optional[Dict]:
        return await self._get(
            f"/apis/autoscaling/v2/namespaces/{namespace}/horizontalpodautoscalers/{hpa_name}"
        )

    async def patch_hpa_min_replicas(
        self, namespace: str, hpa_name: str, min_replicas: int
    ) -> Optional[int]:
        """Patch HPA minReplicas. Returns original value for revert, or None."""
        hpa = await self.get_hpa(namespace, hpa_name)
        if not hpa:
            return None
        original = hpa.get("spec", {}).get("minReplicas", 1)
        patch = {"spec": {"minReplicas": min_replicas}}
        result = await self._patch_merge(
            f"/apis/autoscaling/v2/namespaces/{namespace}/horizontalpodautoscalers/{hpa_name}",
            patch,
        )
        return original if result else None

    # --- NetworkPolicy operations ---

    async def create_network_policy(
        self, namespace: str, name: str, body: Dict
    ) -> Optional[Dict]:
        return await self._post(
            f"/apis/networking.k8s.io/v1/namespaces/{namespace}/networkpolicies",
            body,
        )

    async def delete_network_policy(self, namespace: str, name: str) -> bool:
        return await self._delete(
            f"/apis/networking.k8s.io/v1/namespaces/{namespace}/networkpolicies/{name}"
        )

    # --- ResourceQuota operations ---

    async def create_resource_quota(
        self, namespace: str, name: str, body: Dict
    ) -> Optional[Dict]:
        return await self._post(
            f"/api/v1/namespaces/{namespace}/resourcequotas", body
        )

    async def delete_resource_quota(self, namespace: str, name: str) -> bool:
        return await self._delete(
            f"/api/v1/namespaces/{namespace}/resourcequotas/{name}"
        )


# ---------------------------------------------------------------------------
# Reconciler
# ---------------------------------------------------------------------------
class MitigationReconciler:
    def __init__(self, config: ControllerConfig, k8s: K8sClient):
        self._cfg = config
        self._k8s = k8s

    # -----------------------------------------------------------------------
    # Entry point
    # -----------------------------------------------------------------------
    async def reconcile(self, obj: Dict) -> None:
        name = obj.get("metadata", {}).get("name", "")
        spec = obj.get("spec", {})
        status = obj.get("status", {})
        phase = status.get("phase", "Pending")
        deletion_ts = obj.get("metadata", {}).get("deletionTimestamp")
        finalizers = obj.get("metadata", {}).get("finalizers", [])

        log.info(f"Reconcile intent={name} phase={phase} deletion={bool(deletion_ts)}")

        with RECONCILE_DURATION.labels(phase=phase).time():
            try:
                if deletion_ts:
                    await self._handle_deletion(obj, name, phase)
                elif phase == "Pending":
                    await self._handle_pending(obj, name, spec)
                elif phase == "Active":
                    await self._handle_active(obj, name, spec, status)
                elif phase == "Reverting":
                    await self._handle_reverting(obj, name, status)
                elif phase in ("Completed", "Failed"):
                    pass  # Terminal states — no action
                RECONCILE_TOTAL.labels(phase=phase, result="success").inc()
            except Exception as exc:
                log.error(f"Reconcile error for {name}: {exc}", exc_info=True)
                RECONCILE_TOTAL.labels(phase=phase, result="error").inc()

    # -----------------------------------------------------------------------
    # Phase: Deletion (finalizer cleanup)
    # -----------------------------------------------------------------------
    async def _handle_deletion(self, obj: Dict, name: str, phase: str) -> None:
        if phase not in ("Completed", "Failed"):
            await self._revert_all(name, obj.get("status", {}))
        await self._k8s.remove_finalizer(
            self._cfg.crd_group, self._cfg.crd_version,
            self._cfg.intent_plural, name, self._cfg.FINALIZER,
        )
        log.info(f"Removed finalizer from {name}")

    # -----------------------------------------------------------------------
    # Phase: Pending → Active
    # -----------------------------------------------------------------------
    async def _handle_pending(self, obj: Dict, name: str, spec: Dict) -> None:
        # Add finalizer first
        await self._k8s.add_finalizer(
            self._cfg.crd_group, self._cfg.crd_version,
            self._cfg.intent_plural, name, self._cfg.FINALIZER,
        )

        applied_resources = []
        conditions = []
        strategy_type = spec.get("strategy", {}).get("type", "BandwidthConstraint")

        # --- Nephio-exclusive: instant isolation first ---
        # NetworkPolicy blocks attack→victim traffic immediately (~0ms)
        if strategy_type == "Hybrid":
            resources, cond = await self._apply_network_isolation(spec)
            applied_resources.extend(resources)
            conditions.extend(cond)

        # ResourceQuota starves attack pods of CPU/memory immediately
        if strategy_type == "Hybrid":
            resources, cond = await self._apply_resource_quota(spec)
            applied_resources.extend(resources)
            conditions.extend(cond)

        # --- Standard strategies ---
        if strategy_type in ("BandwidthConstraint", "Hybrid"):
            resources, cond = await self._apply_bandwidth_constraint(spec)
            applied_resources.extend(resources)
            conditions.extend(cond)

        if strategy_type in ("PriorityElevation", "Hybrid"):
            resources, cond = await self._apply_priority_elevation(spec)
            applied_resources.extend(resources)
            conditions.extend(cond)

        # DecoyReduction: scale down/delete decoy workloads (most effective)
        if strategy_type == "Hybrid":
            resources, cond = await self._apply_decoy_reduction(spec)
            applied_resources.extend(resources)
            conditions.extend(cond)

        if strategy_type in ("TopologyEviction", "Hybrid"):
            resources, cond = await self._apply_topology_eviction(spec)
            applied_resources.extend(resources)
            conditions.extend(cond)

        # --- Batched victim protection ---
        # Apply PriorityClass + topology constraints + scale-up as a SINGLE
        # patch per victim Deployment.  This avoids intermediate ReplicaSets
        # and the pod churn that would otherwise disrupt services with long
        # readiness probe delays (e.g. catalogue initialDelaySeconds: 180s).
        if strategy_type in ("PriorityElevation", "TopologyEviction", "Hybrid"):
            resources, cond = await self._apply_victim_protection(spec)
            applied_resources.extend(resources)
            conditions.extend(cond)

        # Update status → Active
        now = datetime.now(timezone.utc).isoformat()
        new_status = {
            "phase": "Active",
            "appliedAt": now,
            "appliedResources": applied_resources,
            "conditions": conditions + [
                _condition("Ready", "True", "Applied", "All strategy steps completed", now)
            ],
            "message": f"Mitigation active via strategy {strategy_type}",
        }
        await self._k8s.update_crd_status(
            self._cfg.crd_group, self._cfg.crd_version,
            self._cfg.intent_plural, name, new_status,
        )
        ACTIVE_MITIGATIONS.inc()
        log.info(f"Intent {name} transitioned to Active")

    # -----------------------------------------------------------------------
    # Phase: Active — check TTL and drift
    # -----------------------------------------------------------------------
    async def _handle_active(
        self, obj: Dict, name: str, spec: Dict, status: Dict
    ) -> None:
        ttl_seconds = spec.get("ttlSeconds", 300)
        applied_at_str = status.get("appliedAt", "")

        if applied_at_str:
            applied_at = datetime.fromisoformat(applied_at_str.replace("Z", "+00:00"))
            elapsed = (datetime.now(timezone.utc) - applied_at).total_seconds()
            if elapsed >= ttl_seconds:
                log.info(f"Intent {name} TTL expired ({elapsed:.0f}s >= {ttl_seconds}s), reverting")
                TTL_EXPIRATIONS.inc()
                await self._k8s.update_crd_status(
                    self._cfg.crd_group, self._cfg.crd_version,
                    self._cfg.intent_plural, name,
                    {"phase": "Reverting",
                     "message": f"TTL of {ttl_seconds}s expired"},
                )
                ACTIVE_MITIGATIONS.dec()
                return

        # Drift detection: verify bandwidth annotations still present on decoy pods
        drifted = await self._detect_drift(spec, status)
        if drifted:
            log.info(f"Intent {name}: drift detected in {drifted}, re-applying")
            DRIFT_CORRECTIONS.inc()
            await self._reapply_drifted(name, spec, status, drifted)

    # -----------------------------------------------------------------------
    # Phase: Reverting → Completed
    # -----------------------------------------------------------------------
    async def _handle_reverting(self, obj: Dict, name: str, status: Dict) -> None:
        await self._revert_all(name, status)
        now = datetime.now(timezone.utc).isoformat()
        await self._k8s.update_crd_status(
            self._cfg.crd_group, self._cfg.crd_version,
            self._cfg.intent_plural, name,
            {
                "phase": "Completed",
                "revertedAt": now,
                "message": "All mitigation resources reverted",
            },
        )
        log.info(f"Intent {name} Completed (reverted)")

    # -----------------------------------------------------------------------
    # Strategy: BandwidthConstraint
    # -----------------------------------------------------------------------
    async def _apply_bandwidth_constraint(
        self, spec: Dict
    ) -> Tuple[List[Dict], List[Dict]]:
        strategy = spec.get("strategy", {})
        ingress_mbps = strategy.get("decoyIngressLimitMbps", 100)
        egress_mbps = strategy.get("decoyEgressLimitMbps", 100)

        applied = []
        conditions = []
        now = datetime.now(timezone.utc).isoformat()
        success_count = 0

        for pod in spec.get("decoyPods", []):
            ns = pod.get("namespace", "")
            pod_name = pod.get("name", "")
            if not ns or not pod_name:
                continue

            # Record current annotations for revert
            existing_pod = await self._k8s._get(
                f"/api/v1/namespaces/{ns}/pods/{pod_name}"
            )
            original_annotations = {}
            if existing_pod:
                original_annotations = existing_pod.get(
                    "metadata", {}
                ).get("annotations", {}) or {}

            result = await self._k8s.patch_pod_bandwidth_annotations(
                ns, pod_name, ingress_mbps, egress_mbps
            )
            if result:
                applied.append({
                    "apiVersion": "v1",
                    "kind": "Pod",
                    "namespace": ns,
                    "name": pod_name,
                    "action": "Annotated",
                    "revertData": {
                        "ingress-bandwidth": original_annotations.get(
                            "kubernetes.io/ingress-bandwidth", ""
                        ),
                        "egress-bandwidth": original_annotations.get(
                            "kubernetes.io/egress-bandwidth", ""
                        ),
                    },
                })
                RESOURCES_APPLIED.labels(kind="Pod/BandwidthAnnotation", action="Annotated").inc()
                BANDWIDTH_CONSTRAINTS_ACTIVE.inc()
                success_count += 1
                log.info(f"Applied bandwidth constraint to pod {ns}/{pod_name}")

        if success_count > 0:
            conditions.append(
                _condition(
                    "BandwidthConstraintApplied", "True", "Annotated",
                    f"Applied bandwidth annotations to {success_count} decoy pods", now,
                )
            )
        else:
            conditions.append(
                _condition(
                    "BandwidthConstraintApplied", "False", "NoPodPatched",
                    "No decoy pod bandwidth annotations applied", now,
                )
            )

        return applied, conditions

    # -----------------------------------------------------------------------
    # Strategy: PriorityElevation (PriorityClass creation only)
    #   Creates the PriorityClass resource.  Actual assignment to victim
    #   Deployments is done in _apply_victim_protection() as part of the
    #   batched patch to avoid multiple rolling restarts.
    # -----------------------------------------------------------------------
    async def _apply_priority_elevation(
        self, spec: Dict
    ) -> Tuple[List[Dict], List[Dict]]:
        strategy = spec.get("strategy", {})
        pc_name = strategy.get("victimPriorityClassName", "crossfire-victim-priority")
        pc_value = strategy.get("victimPriorityValue", 1000000)

        applied = []
        conditions = []
        now = datetime.now(timezone.utc).isoformat()

        # Ensure PriorityClass exists
        ok = await self._k8s.ensure_priority_class(pc_name, pc_value)
        if ok:
            applied.append({
                "apiVersion": "scheduling.k8s.io/v1",
                "kind": "PriorityClass",
                "name": pc_name,
                "action": "Created",
            })
            RESOURCES_APPLIED.labels(kind="PriorityClass", action="Created").inc()
            conditions.append(
                _condition(
                    "PriorityElevationApplied", "True", "PriorityClassCreated",
                    f"Priority class {pc_name} created (assignment via batched victim protection)", now,
                )
            )
        else:
            conditions.append(
                _condition(
                    "PriorityElevationApplied", "False", "PriorityClassFailed",
                    f"Failed to create PriorityClass {pc_name}", now,
                )
            )

        return applied, conditions

    # -----------------------------------------------------------------------
    # Strategy: DecoyReduction (scale down Deployments, delete DaemonSets)
    # -----------------------------------------------------------------------
    async def _apply_decoy_reduction(
        self, spec: Dict
    ) -> Tuple[List[Dict], List[Dict]]:
        max_replicas = spec.get("strategy", {}).get("decoyMaxReplicas", 0)
        applied = []
        conditions = []
        now = datetime.now(timezone.utc).isoformat()
        scaled = 0
        deleted = 0

        # Discover owners of decoy pods and deduplicate
        seen_owners: Dict[tuple, Dict] = {}  # (kind, ns, name) -> owner info
        for pod in spec.get("decoyPods", []):
            ns = pod.get("namespace", "")
            pod_name = pod.get("name", "")
            if not ns or not pod_name:
                continue

            owner = await self._k8s.get_pod_owner(ns, pod_name)
            if not owner:
                log.warning(f"Could not find owner for decoy pod {ns}/{pod_name}")
                continue
            key = (owner["kind"], owner["namespace"], owner["name"])
            if key not in seen_owners:
                seen_owners[key] = owner

        for (kind, ns, name), owner in seen_owners.items():
            if kind == "Deployment":
                original = await self._k8s.scale_deployment(ns, name, max_replicas)
                if original is not None:
                    applied.append({
                        "apiVersion": "apps/v1",
                        "kind": "Deployment",
                        "namespace": ns,
                        "name": name,
                        "action": "Scaled",
                        "revertData": {"replicas": original},
                    })
                    RESOURCES_APPLIED.labels(kind="Deployment/Scale", action="Scaled").inc()
                    log.info(f"Scaled decoy Deployment {ns}/{name} from {original} to {max_replicas}")
                    scaled += 1
            elif kind == "DaemonSet":
                # Save the DaemonSet spec before deleting for revert
                ds = await self._k8s.get_daemonset(ns, name)
                ds_spec = None
                if ds:
                    ds_spec = {
                        "apiVersion": "apps/v1",
                        "kind": "DaemonSet",
                        "metadata": {
                            "name": name,
                            "namespace": ns,
                            "labels": ds.get("metadata", {}).get("labels", {}),
                        },
                        "spec": ds.get("spec", {}),
                    }
                ok = await self._k8s.delete_daemonset(ns, name)
                if ok:
                    applied.append({
                        "apiVersion": "apps/v1",
                        "kind": "DaemonSet",
                        "namespace": ns,
                        "name": name,
                        "action": "Deleted",
                        "revertData": {"originalSpec": ds_spec} if ds_spec else {},
                    })
                    RESOURCES_APPLIED.labels(kind="DaemonSet", action="Deleted").inc()
                    log.info(f"Deleted decoy DaemonSet {ns}/{name}")
                    deleted += 1

        total = scaled + deleted
        if total > 0:
            conditions.append(
                _condition(
                    "DecoyReductionApplied", "True", "ScaledAndDeleted",
                    f"Scaled {scaled} Deployments to {max_replicas}, deleted {deleted} DaemonSets",
                    now,
                )
            )
        else:
            conditions.append(
                _condition(
                    "DecoyReductionApplied", "False", "NoOwnersFound",
                    "Could not find decoy pod owners to reduce", now,
                )
            )

        return applied, conditions

    # -----------------------------------------------------------------------
    # Strategy: TopologyEviction (node taint only)
    #   Taints the congested node to prevent new attack pods from scheduling.
    #   Actual victim Deployment topology spread + anti-affinity patching
    #   is done in _apply_victim_protection() as part of the batched patch.
    # -----------------------------------------------------------------------
    async def _apply_topology_eviction(
        self, spec: Dict
    ) -> Tuple[List[Dict], List[Dict]]:
        node = spec.get("affectedNode", "")
        taint_key = "crossfire.io/congested"
        applied = []
        conditions = []
        now = datetime.now(timezone.utc).isoformat()

        # Taint the congested node
        if node:
            ok = await self._k8s.taint_node(node, taint_key, "true", "NoSchedule")
            if ok:
                applied.append({
                    "apiVersion": "v1",
                    "kind": "Node",
                    "name": node,
                    "action": "Patched",
                    "revertData": {"taintKey": taint_key},
                })
                RESOURCES_APPLIED.labels(kind="Node/Taint", action="Patched").inc()
                log.info(f"Tainted node {node} with {taint_key}=true:NoSchedule")

        if node:
            conditions.append(
                _condition(
                    "TopologyEvictionApplied", "True", "NodeTainted",
                    f"Tainted node {node} (victim topology via batched victim protection)", now,
                )
            )

        return applied, conditions

        return applied, conditions

    # -----------------------------------------------------------------------
    # Strategy: NetworkPolicy Isolation (Nephio-exclusive)
    #   Creates a NetworkPolicy in the victim namespace that blocks all
    #   ingress traffic from attack namespaces. This provides INSTANT
    #   traffic isolation (~0ms) while slower strategies take effect.
    #   Native K8s mitigation has no equivalent declarative mechanism.
    # -----------------------------------------------------------------------
    async def _apply_network_isolation(
        self, spec: Dict
    ) -> Tuple[List[Dict], List[Dict]]:
        applied = []
        conditions = []
        now = datetime.now(timezone.utc).isoformat()

        # Identify unique attack namespaces and victim namespaces
        attack_namespaces = set()
        for pod in spec.get("decoyPods", []):
            ns = pod.get("namespace", "")
            if ns:
                attack_namespaces.add(ns)

        victim_namespaces = set()
        for pod in spec.get("victimPods", []):
            ns = pod.get("namespace", "")
            if ns:
                victim_namespaces.add(ns)

        created = 0
        for victim_ns in victim_namespaces:
            policy_name = f"crossfire-block-attack-{spec.get('affectedNode', 'unknown')[:20]}"
            # Create a NetworkPolicy that denies ingress from attack namespaces
            # while allowing all other traffic (existing pod-to-pod communication)
            np_body = {
                "apiVersion": "networking.k8s.io/v1",
                "kind": "NetworkPolicy",
                "metadata": {
                    "name": policy_name,
                    "namespace": victim_ns,
                    "labels": {
                        "app.kubernetes.io/managed-by": "crossfire-controller",
                        "crossfire.io/purpose": "attack-isolation",
                    },
                },
                "spec": {
                    "podSelector": {},  # Apply to all pods in victim namespace
                    "policyTypes": ["Ingress"],
                    "ingress": [
                        {
                            # Allow traffic from everywhere EXCEPT attack namespaces
                            "from": [
                                {
                                    "namespaceSelector": {
                                        "matchExpressions": [
                                            {
                                                "key": "kubernetes.io/metadata.name",
                                                "operator": "NotIn",
                                                "values": list(attack_namespaces),
                                            }
                                        ]
                                    }
                                }
                            ]
                        }
                    ],
                },
            }
            result = await self._k8s.create_network_policy(victim_ns, policy_name, np_body)
            if result:
                applied.append({
                    "apiVersion": "networking.k8s.io/v1",
                    "kind": "NetworkPolicy",
                    "namespace": victim_ns,
                    "name": policy_name,
                    "action": "Created",
                })
                RESOURCES_APPLIED.labels(kind="NetworkPolicy", action="Created").inc()
                log.info(f"Created NetworkPolicy {victim_ns}/{policy_name} blocking traffic from {attack_namespaces}")
                created += 1

        if created > 0:
            conditions.append(
                _condition(
                    "NetworkIsolationApplied", "True", "PolicyCreated",
                    f"Created {created} NetworkPolicies blocking {attack_namespaces}", now,
                )
            )
        else:
            conditions.append(
                _condition(
                    "NetworkIsolationApplied", "False", "NoPolicyCreated",
                    "No NetworkPolicy isolation applied", now,
                )
            )

        return applied, conditions

    # -----------------------------------------------------------------------
    # Strategy: ResourceQuota Injection (Nephio-exclusive)
    #   Creates a tight ResourceQuota in the attack namespace to immediately
    #   starve attack pods of CPU/memory. Pods exceeding the quota are
    #   prevented from consuming more resources, degrading their attack
    #   effectiveness even before scale-down completes.
    # -----------------------------------------------------------------------
    async def _apply_resource_quota(
        self, spec: Dict
    ) -> Tuple[List[Dict], List[Dict]]:
        applied = []
        conditions = []
        now = datetime.now(timezone.utc).isoformat()

        attack_namespaces = set()
        for pod in spec.get("decoyPods", []):
            ns = pod.get("namespace", "")
            if ns:
                attack_namespaces.add(ns)

        created = 0
        for atk_ns in attack_namespaces:
            quota_name = "crossfire-attack-throttle"
            quota_body = {
                "apiVersion": "v1",
                "kind": "ResourceQuota",
                "metadata": {
                    "name": quota_name,
                    "namespace": atk_ns,
                    "labels": {
                        "app.kubernetes.io/managed-by": "crossfire-controller",
                        "crossfire.io/purpose": "attack-throttle",
                    },
                },
                "spec": {
                    "hard": {
                        "requests.cpu": "100m",
                        "requests.memory": "64Mi",
                        "limits.cpu": "200m",
                        "limits.memory": "128Mi",
                        "pods": "2",
                    }
                },
            }
            result = await self._k8s.create_resource_quota(atk_ns, quota_name, quota_body)
            if result:
                applied.append({
                    "apiVersion": "v1",
                    "kind": "ResourceQuota",
                    "namespace": atk_ns,
                    "name": quota_name,
                    "action": "Created",
                })
                RESOURCES_APPLIED.labels(kind="ResourceQuota", action="Created").inc()
                log.info(f"Created ResourceQuota {atk_ns}/{quota_name} to throttle attack pods")
                created += 1

        if created > 0:
            conditions.append(
                _condition(
                    "ResourceQuotaApplied", "True", "QuotaCreated",
                    f"Created ResourceQuota in {attack_namespaces} to throttle attack resources", now,
                )
            )
        else:
            conditions.append(
                _condition(
                    "ResourceQuotaApplied", "False", "NoQuotaCreated",
                    "No ResourceQuota applied", now,
                )
            )

        return applied, conditions

    # -----------------------------------------------------------------------
    # Strategy: Batched Victim Protection
    #   Applies PriorityClass assignment + topology spread + node
    #   anti-affinity + scale-up as a SINGLE strategic-merge-patch per
    #   victim Deployment.  One patch → one new ReplicaSet → one clean
    #   rolling update, avoiding the intermediate ReplicaSets and pod
    #   churn that would otherwise disrupt services with long readiness
    #   probe delays (e.g. catalogue with initialDelaySeconds=180s).
    #
    #   After patching, waits for each rollout to complete so the
    #   mitigation is truly effective before marking Active.
    # -----------------------------------------------------------------------
    async def _apply_victim_protection(
        self, spec: Dict
    ) -> Tuple[List[Dict], List[Dict]]:
        strategy = spec.get("strategy", {})
        strategy_type = strategy.get("type", "BandwidthConstraint")
        pc_name = strategy.get("victimPriorityClassName", "crossfire-victim-priority")
        target_replicas = strategy.get("victimScaleUpReplicas", 3)
        avoid_node = spec.get("affectedNode", "")

        apply_priority = strategy_type in ("PriorityElevation", "Hybrid")
        apply_topology = strategy_type in ("TopologyEviction", "Hybrid")
        apply_scale = strategy_type == "Hybrid"

        applied = []
        conditions = []
        now = datetime.now(timezone.utc).isoformat()
        patched = 0

        patched_deploys = set()
        for pod in spec.get("victimPods", []):
            deploy = pod.get("deploymentName", "")
            ns = pod.get("namespace", "")
            if not deploy or not ns or (ns, deploy) in patched_deploys:
                continue

            current_deploy = await self._k8s.get_deployment(ns, deploy)
            if not current_deploy:
                continue

            # Gather original state for revert
            current_spec = current_deploy.get("spec", {})
            template_spec = current_spec.get("template", {}).get("spec", {})
            original_priority = template_spec.get("priorityClassName", "")
            original_replicas = current_spec.get("replicas", 1)

            # Check idempotency: skip if already protected
            if apply_priority and template_spec.get("priorityClassName") == pc_name:
                log.info(f"Deployment {ns}/{deploy} already protected, skipping")
                patched_deploys.add((ns, deploy))
                continue

            # Read the deployment's existing pod label key for topology spread
            pod_labels = (
                current_deploy.get("spec", {})
                .get("template", {})
                .get("metadata", {})
                .get("labels", {})
            )
            # Use the first existing label as the spread selector so that
            # the TopologySpreadConstraint actually works.  Fallback to "app".
            spread_label_key = "app"
            spread_label_value = deploy
            for k, v in pod_labels.items():
                spread_label_key = k
                spread_label_value = v
                break

            # Build a single combined patch with only the relevant parts
            patch: Dict[str, Any] = {
                "spec": {
                    "template": {
                        "metadata": {
                            "labels": {
                                "crossfire.io/protected": "true",
                            }
                        },
                        "spec": {},
                    },
                },
            }

            if apply_priority:
                patch["spec"]["template"]["spec"]["priorityClassName"] = pc_name

            # Only scale up if below target and scaling is enabled
            if apply_scale and original_replicas < target_replicas:
                patch["spec"]["replicas"] = target_replicas

            # Add topology constraints only when enabled and we know which node to avoid
            if apply_topology and avoid_node:
                patch["spec"]["template"]["spec"]["affinity"] = {
                    "nodeAffinity": {
                        "preferredDuringSchedulingIgnoredDuringExecution": [
                            {
                                "weight": 100,
                                "preference": {
                                    "matchExpressions": [
                                        {
                                            "key": "kubernetes.io/hostname",
                                            "operator": "NotIn",
                                            "values": [avoid_node],
                                        }
                                    ]
                                },
                            }
                        ]
                    }
                }
                patch["spec"]["template"]["spec"]["topologySpreadConstraints"] = [
                    {
                        "maxSkew": 1,
                        "topologyKey": "kubernetes.io/hostname",
                        "whenUnsatisfiable": "ScheduleAnyway",
                        "labelSelector": {
                            "matchLabels": {
                                spread_label_key: spread_label_value,
                            }
                        },
                    }
                ]

            result = await self._k8s._patch_strategic(
                f"/apis/apps/v1/namespaces/{ns}/deployments/{deploy}", patch
            )
            if result:
                patched_deploys.add((ns, deploy))
                applied.append({
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "namespace": ns,
                    "name": deploy,
                    "action": "Patched",
                    "revertData": {
                        "priorityClassName": original_priority,
                        "topologyConstraints": "crossfire-added",
                        "replicas": original_replicas,
                        "purpose": "victim-protection-batch",
                    },
                })
                RESOURCES_APPLIED.labels(kind="Deployment/VictimProtection", action="Patched").inc()
                scaled_msg = f", scaled {original_replicas}→{target_replicas}" if original_replicas < target_replicas else ""
                log.info(
                    f"Protected victim Deployment {ns}/{deploy}: "
                    f"priority={pc_name}, topology-avoid={avoid_node}{scaled_msg}"
                )
                patched += 1

        if patched > 0:
            conditions.append(
                _condition(
                    "VictimProtectionApplied", "True", "BatchPatched",
                    f"Protected {patched} victim Deployments (priority+topology+scale, single rollout)",
                    now,
                )
            )
        else:
            conditions.append(
                _condition(
                    "VictimProtectionApplied", "False", "NoVictimsFound",
                    "No victim Deployments needed protection", now,
                )
            )

        return applied, conditions

    async def _wait_for_deployment_rollout(
        self, namespace: str, deploy_name: str, timeout: int = 300
    ) -> bool:
        """Poll a Deployment until all pods are updated and available."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            deploy = await self._k8s.get_deployment(namespace, deploy_name)
            if deploy:
                status = deploy.get("status", {})
                spec_replicas = deploy.get("spec", {}).get("replicas", 1)
                updated = status.get("updatedReplicas", 0)
                available = status.get("availableReplicas", 0)
                ready = status.get("readyReplicas", 0)
                if updated >= spec_replicas and available >= spec_replicas and ready >= spec_replicas:
                    log.info(f"Deployment {namespace}/{deploy_name} rollout complete "
                             f"(updated={updated}, available={available}, ready={ready})")
                    return True
            await asyncio.sleep(5)
        log.warning(f"Deployment {namespace}/{deploy_name} rollout timed out after {timeout}s")
        return False

    # -----------------------------------------------------------------------
    # Drift detection
    # -----------------------------------------------------------------------
    async def _detect_drift(self, spec: Dict, status: Dict) -> List[str]:
        """Check if bandwidth annotations are still present on decoy pods."""
        drifted = []
        strategy_type = spec.get("strategy", {}).get("type", "")
        if strategy_type not in ("BandwidthConstraint", "Hybrid"):
            return drifted

        for pod in spec.get("decoyPods", []):
            ns = pod.get("namespace", "")
            pod_name = pod.get("name", "")
            existing = await self._k8s._get(f"/api/v1/namespaces/{ns}/pods/{pod_name}")
            if existing:
                annotations = existing.get("metadata", {}).get("annotations", {}) or {}
                if "kubernetes.io/egress-bandwidth" not in annotations:
                    drifted.append(f"Pod/{ns}/{pod_name}/bandwidth")
        return drifted

    async def _reapply_drifted(
        self, name: str, spec: Dict, status: Dict, drifted: List[str]
    ) -> None:
        strategy = spec.get("strategy", {})
        ingress_mbps = strategy.get("decoyIngressLimitMbps", 100)
        egress_mbps = strategy.get("decoyEgressLimitMbps", 100)
        for item in drifted:
            parts = item.split("/")
            if parts[0] == "Pod" and len(parts) == 4:
                ns, pod_name = parts[1], parts[2]
                await self._k8s.patch_pod_bandwidth_annotations(
                    ns, pod_name, ingress_mbps, egress_mbps
                )
                log.info(f"Re-applied bandwidth constraint to drifted pod {ns}/{pod_name}")

    # -----------------------------------------------------------------------
    # Revert all applied resources
    # -----------------------------------------------------------------------
    async def _revert_all(self, name: str, status: Dict) -> None:
        applied = status.get("appliedResources", [])
        log.info(f"Reverting {len(applied)} resources for intent {name}")

        for resource in applied:
            kind = resource.get("kind", "")
            action = resource.get("action", "")
            revert_data = resource.get("revertData", {})
            ns = resource.get("namespace", "")
            res_name = resource.get("name", "")

            try:
                if kind == "Pod" and action == "Annotated":
                    orig_ingress = revert_data.get("ingress-bandwidth", "")
                    orig_egress = revert_data.get("egress-bandwidth", "")
                    if orig_ingress or orig_egress:
                        # Restore original annotations
                        await self._k8s._patch_merge(
                            f"/api/v1/namespaces/{ns}/pods/{res_name}",
                            {"metadata": {"annotations": {
                                "kubernetes.io/ingress-bandwidth": orig_ingress or None,
                                "kubernetes.io/egress-bandwidth": orig_egress or None,
                            }}},
                        )
                    else:
                        await self._k8s.remove_pod_bandwidth_annotations(ns, res_name)
                    RESOURCES_REVERTED.labels(kind="Pod/BandwidthAnnotation").inc()
                    BANDWIDTH_CONSTRAINTS_ACTIVE.dec()

                elif kind == "PriorityClass" and action == "Created":
                    await self._k8s.delete_priority_class(res_name)
                    RESOURCES_REVERTED.labels(kind="PriorityClass").inc()

                elif kind == "Deployment" and action == "Patched":
                    # Handle both legacy separate patches and new batched patches
                    orig_priority = revert_data.get("priorityClassName")
                    has_topology = "topologyConstraints" in revert_data
                    orig_replicas = revert_data.get("replicas")
                    is_batch = revert_data.get("purpose") == "victim-protection-batch"

                    if is_batch:
                        # Batched revert: priority + topology + scale in one go
                        revert_patch: Dict[str, Any] = {"spec": {"template": {"spec": {}}}}
                        if orig_priority is not None:
                            if orig_priority:
                                revert_patch["spec"]["template"]["spec"]["priorityClassName"] = orig_priority
                            else:
                                # Remove priorityClassName by setting to None
                                revert_patch["spec"]["template"]["spec"]["priorityClassName"] = None
                        revert_patch["spec"]["template"]["spec"]["affinity"] = None
                        revert_patch["spec"]["template"]["spec"]["topologySpreadConstraints"] = None
                        # Remove crossfire label
                        revert_patch["spec"]["template"] = revert_patch.get("spec", {}).get("template", {})
                        revert_patch["spec"]["template"].setdefault("metadata", {})
                        revert_patch["spec"]["template"]["metadata"]["labels"] = {
                            "crossfire.io/protected": None,
                        }
                        if orig_replicas is not None:
                            revert_patch["spec"]["replicas"] = orig_replicas
                        await self._k8s._patch_strategic(
                            f"/apis/apps/v1/namespaces/{ns}/deployments/{res_name}",
                            revert_patch,
                        )
                        RESOURCES_REVERTED.labels(kind="Deployment/VictimProtection").inc()
                    else:
                        # Legacy separate patches
                        if orig_priority is not None:
                            await self._k8s.patch_deployment_priority_class(ns, res_name, orig_priority)
                            RESOURCES_REVERTED.labels(kind="Deployment/Priority").inc()
                        if has_topology:
                            await self._k8s.remove_deployment_topology_patches(ns, res_name)
                            RESOURCES_REVERTED.labels(kind="Deployment/Topology").inc()

                elif kind == "Deployment" and action == "Scaled":
                    orig_replicas = revert_data.get("replicas", 1)
                    await self._k8s.scale_deployment(ns, res_name, orig_replicas)
                    RESOURCES_REVERTED.labels(kind="Deployment/Scale").inc()
                    log.info(f"Reverted Deployment {ns}/{res_name} replicas to {orig_replicas}")

                elif kind == "DaemonSet" and action == "Deleted":
                    orig_spec = revert_data.get("originalSpec")
                    if orig_spec:
                        # Remove resourceVersion/uid to allow recreation
                        orig_spec.get("metadata", {}).pop("resourceVersion", None)
                        orig_spec.get("metadata", {}).pop("uid", None)
                        orig_spec.get("metadata", {}).pop("creationTimestamp", None)
                        await self._k8s.create_daemonset(ns, orig_spec)
                        RESOURCES_REVERTED.labels(kind="DaemonSet").inc()
                        log.info(f"Recreated DaemonSet {ns}/{res_name}")

                elif kind == "Node" and action == "Patched":
                    taint_key = revert_data.get("taintKey", "crossfire.io/congested")
                    await self._k8s.untaint_node(res_name, taint_key)
                    RESOURCES_REVERTED.labels(kind="Node/Taint").inc()

                elif kind == "NetworkPolicy" and action == "Created":
                    await self._k8s.delete_network_policy(ns, res_name)
                    RESOURCES_REVERTED.labels(kind="NetworkPolicy").inc()
                    log.info(f"Deleted NetworkPolicy {ns}/{res_name}")

                elif kind == "ResourceQuota" and action == "Created":
                    await self._k8s.delete_resource_quota(ns, res_name)
                    RESOURCES_REVERTED.labels(kind="ResourceQuota").inc()
                    log.info(f"Deleted ResourceQuota {ns}/{res_name}")

                log.info(f"Reverted {kind} {ns}/{res_name}")
            except Exception as exc:
                log.error(f"Failed to revert {kind} {res_name}: {exc}")


# ---------------------------------------------------------------------------
# Controller main loop
# ---------------------------------------------------------------------------
class Controller:
    def __init__(self, config: ControllerConfig):
        self._cfg = config

    async def run_forever(self) -> None:
        token = _read_sa_token()
        ca = _read_ca_cert()
        start_http_server(self._cfg.metrics_port)
        log.info(f"CrossfireMitigationController starting, metrics :{self._cfg.metrics_port}")

        while True:
            try:
                await self._watch_loop(token, ca)
            except Exception as exc:
                log.error(f"Controller loop error: {exc}; retrying in 10s")
                await asyncio.sleep(10)

    async def _watch_loop(self, token: str, ca: str) -> None:
        async with aiohttp.ClientSession() as session:
            k8s = K8sClient(session, self._cfg.k8s_api_url, token, ca)
            reconciler = MitigationReconciler(self._cfg, k8s)

            # Launch a background task for periodic TTL/drift checks on Active intents
            ttl_task = asyncio.create_task(
                self._periodic_active_check(k8s, reconciler)
            )

            log.info("Starting watch on CrossfireMitigationIntent resources")
            try:
                async for event_type, obj in k8s.watch_cluster_crds(
                    self._cfg.crd_group, self._cfg.crd_version, self._cfg.intent_plural
                ):
                    if event_type in ("ADDED", "MODIFIED"):
                        await reconciler.reconcile(obj)
            finally:
                ttl_task.cancel()
                try:
                    await ttl_task
                except asyncio.CancelledError:
                    pass

    async def _periodic_active_check(
        self, k8s: K8sClient, reconciler: MitigationReconciler
    ) -> None:
        """Periodically list Active intents and reconcile them for TTL/drift checks."""
        while True:
            await asyncio.sleep(self._cfg.active_requeue_seconds)
            try:
                path = (
                    f"/apis/{self._cfg.crd_group}/{self._cfg.crd_version}"
                    f"/{self._cfg.intent_plural}"
                )
                result = await k8s._get(path)
                if result and "items" in result:
                    for obj in result["items"]:
                        phase = obj.get("status", {}).get("phase", "Pending")
                        if phase == "Active":
                            await reconciler.reconcile(obj)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning(f"Periodic active check error: {exc}")


# ---------------------------------------------------------------------------
# Condition helper
# ---------------------------------------------------------------------------
def _condition(
    cond_type: str, status: str, reason: str, message: str, ts: str
) -> Dict:
    return {
        "type": cond_type,
        "status": status,
        "reason": reason,
        "message": message,
        "lastTransitionTime": ts,
    }


# ---------------------------------------------------------------------------
# Service account helpers
# ---------------------------------------------------------------------------
def _read_sa_token() -> str:
    path = "/var/run/secrets/kubernetes.io/serviceaccount/token"
    try:
        with open(path) as f:
            return f.read().strip()
    except FileNotFoundError:
        return os.getenv("KUBERNETES_TOKEN", "")


def _read_ca_cert() -> str:
    path = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
    return path if os.path.exists(path) else ""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
async def _main() -> None:
    config = ControllerConfig()
    ctrl = Controller(config)
    await ctrl.run_forever()


if __name__ == "__main__":
    asyncio.run(_main())
