#!/usr/bin/env python3
"""
Nephio DDoSProtection Controller
==================================
Watches DDoSProtection CRs (workload.nephio.org/v1alpha1) and reconciles
them into concrete Kubernetes resources:
  - NetworkPolicies  — anti-crossfire isolation driven by service types
  - HPAs             — scale thresholds driven by protectionLevel
  - ResourceQuotas   — namespace limits driven by protectionLevel

This is the 4th experiment scenario: instead of pre-authored translated/
manifests, the controller reads intent fields at runtime and generates
resources dynamically.  Changing the CR (e.g. upgrading protectionLevel)
causes the controller to automatically update every generated resource.

Usage:
  kopf run --all-namespaces mitigation/nephio/controller/nephio_controller.py
"""

import kopf
import kubernetes
from kubernetes import client as k8s

# protectionLevel → (hpa_min, hpa_max, cpu_utilization_target%, scaleup_stabilization_s)
LEVEL_HPA = {
    "low":     (1,  5,  90, 120),
    "medium":  (1, 10,  80,  60),
    "high":    (1, 20,  70,  30),
    "maximum": (2, 30,  60,  15),
}

# protectionLevel → (quota_requests_cpu, quota_requests_memory)
LEVEL_QUOTA = {
    "low":     ("4",  "8Gi"),
    "medium":  ("8",  "16Gi"),
    "high":    ("16", "32Gi"),
    "maximum": ("32", "64Gi"),
}

MANAGED_LABELS = {
    "nephio.org/controller": "ddos-protection",
    "nephio.org/managed": "true",
}


@kopf.on.startup()
def configure(settings: kopf.OperatorSettings, **_):
    settings.persistence.finalizer = "nephio.org/controller-finalizer"


@kopf.on.create("workload.nephio.org", "v1alpha1", "ddosprotections")
@kopf.on.update("workload.nephio.org", "v1alpha1", "ddosprotections")
def reconcile(spec, name, namespace, logger, **kwargs):
    kubernetes.config.load_kube_config()
    core_v1 = k8s.CoreV1Api()
    net_v1 = k8s.NetworkingV1Api()
    autoscaling_v2 = k8s.AutoscalingV2Api()

    intent = spec.get("intent", {})
    target_ns = intent.get("targetNamespace", namespace)
    level = intent.get("protectionLevel", "medium")
    services = intent.get("services", [])

    logger.info(
        f"Reconciling DDoSProtection '{name}' → "
        f"namespace={target_ns}, level={level}, services={len(services)}"
    )

    gateways  = [s["name"] for s in services if s.get("type") == "gateway"]
    decoys    = [s["name"] for s in services if s.get("type") == "decoy"]
    criticals = [s["name"] for s in services if s.get("type") == "critical"]

    logger.info(f"  gateways={gateways}, decoys={decoys}, criticals={criticals}")

    _apply_network_policies(net_v1, target_ns, gateways, decoys, criticals, logger)
    _apply_hpas(autoscaling_v2, target_ns, gateways + decoys + criticals, level, logger)
    _apply_resource_quota(core_v1, target_ns, level, name, logger)

    logger.info(f"Reconciliation complete for '{name}'")
    return {"reconciled": True, "level": level, "services": len(services)}


@kopf.on.delete("workload.nephio.org", "v1alpha1", "ddosprotections")
def cleanup(spec, name, namespace, logger, **kwargs):
    kubernetes.config.load_kube_config()
    core_v1 = k8s.CoreV1Api()
    net_v1 = k8s.NetworkingV1Api()
    autoscaling_v2 = k8s.AutoscalingV2Api()

    intent = spec.get("intent", {})
    target_ns = intent.get("targetNamespace", namespace)

    logger.info(f"Cleaning up resources for DDoSProtection '{name}' in {target_ns}")

    label_sel = "nephio.org/controller=ddos-protection"

    try:
        net_v1.delete_collection_namespaced_network_policy(
            namespace=target_ns, label_selector=label_sel
        )
        logger.info(f"  Removed NetworkPolicies in {target_ns}")
    except Exception as e:
        logger.warning(f"  NP cleanup error: {e}")

    try:
        autoscaling_v2.delete_collection_namespaced_horizontal_pod_autoscaler(
            namespace=target_ns, label_selector=label_sel
        )
        logger.info(f"  Removed HPAs in {target_ns}")
    except Exception as e:
        logger.warning(f"  HPA cleanup error: {e}")

    quota_name = f"nephio-ddos-quota-{name}"
    try:
        core_v1.delete_namespaced_resource_quota(name=quota_name, namespace=target_ns)
        logger.info(f"  Removed ResourceQuota {quota_name}")
    except k8s.exceptions.ApiException as e:
        if e.status != 404:
            logger.warning(f"  Quota cleanup error: {e}")

    logger.info(f"Cleanup complete for '{name}'")


# ---------------------------------------------------------------------------
# Resource generators
# ---------------------------------------------------------------------------

def _apply_network_policies(net_v1, ns, gateways, decoys, criticals, logger):
    """Generate and apply NetworkPolicies based on service classification."""

    # 1. Default-deny all ingress in the namespace
    _upsert_network_policy(net_v1, ns, {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": "nephio-default-deny", "namespace": ns, "labels": MANAGED_LABELS},
        "spec": {"podSelector": {}, "policyTypes": ["Ingress"]},
    }, logger)

    # 2. Allow unrestricted ingress to gateway services (they face the internet)
    if gateways:
        _upsert_network_policy(net_v1, ns, {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {
                "name": "nephio-gateway-ingress",
                "namespace": ns,
                "labels": MANAGED_LABELS,
            },
            "spec": {
                "podSelector": {
                    "matchExpressions": [
                        {"key": "name", "operator": "In", "values": gateways}
                    ]
                },
                "policyTypes": ["Ingress"],
                "ingress": [{}],  # open to all sources
            },
        }, logger)

    # 3. Crossfire isolation — decoy services only accept traffic from gateways.
    #    This prevents a crossfire cascade where attacking decoys exhausts
    #    resources shared with critical services.
    if decoys and gateways:
        _upsert_network_policy(net_v1, ns, {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {
                "name": "nephio-crossfire-decoy-isolation",
                "namespace": ns,
                "labels": MANAGED_LABELS,
                "annotations": {
                    "nephio.org/reason": "crossfire-attack-mitigation",
                    "nephio.org/description": (
                        "Isolates decoy services to prevent crossfire cascade. "
                        "Generated from DDoSProtection intent — service type=decoy."
                    ),
                },
            },
            "spec": {
                "podSelector": {
                    "matchExpressions": [
                        {"key": "name", "operator": "In", "values": decoys}
                    ]
                },
                "policyTypes": ["Ingress"],
                "ingress": [
                    {
                        "from": [
                            {
                                "podSelector": {
                                    "matchExpressions": [
                                        {"key": "name", "operator": "In", "values": gateways}
                                    ]
                                }
                            }
                        ]
                    }
                ],
            },
        }, logger)
        logger.info(f"  Applied crossfire isolation: {decoys} ← only from {gateways}")

    # 4. Critical service protection — only gateways (and internal db pods) may connect
    if criticals and gateways:
        _upsert_network_policy(net_v1, ns, {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {
                "name": "nephio-critical-service-protection",
                "namespace": ns,
                "labels": MANAGED_LABELS,
                "annotations": {
                    "nephio.org/reason": "critical-service-isolation",
                    "nephio.org/description": (
                        "Shields critical services from lateral traffic. "
                        "Generated from DDoSProtection intent — service type=critical."
                    ),
                },
            },
            "spec": {
                "podSelector": {
                    "matchExpressions": [
                        {"key": "name", "operator": "In", "values": criticals}
                    ]
                },
                "policyTypes": ["Ingress"],
                "ingress": [
                    {
                        "from": [
                            {
                                "podSelector": {
                                    "matchExpressions": [
                                        {
                                            "key": "name",
                                            "operator": "In",
                                            "values": gateways + ["orders-db", "carts-db", "user-db"],
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                ],
            },
        }, logger)
        logger.info(f"  Applied critical protection: {criticals} ← only from {gateways}")


def _apply_hpas(autoscaling_v2, ns, services, level, logger):
    """Generate an HPA for each service based on the protection level."""
    hpa_min, hpa_max, cpu_target, scaleup_sec = LEVEL_HPA.get(level, LEVEL_HPA["medium"])

    for svc in services:
        hpa_name = f"nephio-hpa-{svc}"
        body = {
            "apiVersion": "autoscaling/v2",
            "kind": "HorizontalPodAutoscaler",
            "metadata": {"name": hpa_name, "namespace": ns, "labels": MANAGED_LABELS},
            "spec": {
                "scaleTargetRef": {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "name": svc,
                },
                "minReplicas": hpa_min,
                "maxReplicas": hpa_max,
                "metrics": [
                    {
                        "type": "Resource",
                        "resource": {
                            "name": "cpu",
                            "target": {
                                "type": "Utilization",
                                "averageUtilization": cpu_target,
                            },
                        },
                    }
                ],
                "behavior": {
                    "scaleUp": {
                        "stabilizationWindowSeconds": scaleup_sec,
                        "policies": [
                            {"type": "Pods", "value": 4, "periodSeconds": 15},
                            {"type": "Percent", "value": 100, "periodSeconds": 15},
                        ],
                        "selectPolicy": "Max",
                    },
                    "scaleDown": {"stabilizationWindowSeconds": 300},
                },
            },
        }

        try:
            autoscaling_v2.read_namespaced_horizontal_pod_autoscaler(
                name=hpa_name, namespace=ns
            )
            autoscaling_v2.replace_namespaced_horizontal_pod_autoscaler(
                name=hpa_name, namespace=ns, body=body
            )
            logger.info(f"  Updated HPA {hpa_name} (level={level}, cpu={cpu_target}%)")
        except k8s.exceptions.ApiException as e:
            if e.status == 404:
                autoscaling_v2.create_namespaced_horizontal_pod_autoscaler(
                    namespace=ns, body=body
                )
                logger.info(f"  Created HPA {hpa_name} (level={level}, cpu={cpu_target}%)")
            else:
                logger.error(f"  HPA error for {svc}: {e}")


def _apply_resource_quota(core_v1, ns, level, cr_name, logger):
    """Apply a ResourceQuota for the namespace based on protection level."""
    cpu_req, mem_req = LEVEL_QUOTA.get(level, LEVEL_QUOTA["medium"])
    cpu_int = int(cpu_req)
    quota_name = f"nephio-ddos-quota-{cr_name}"

    body = {
        "apiVersion": "v1",
        "kind": "ResourceQuota",
        "metadata": {"name": quota_name, "namespace": ns, "labels": MANAGED_LABELS},
        "spec": {
            "hard": {
                "requests.cpu":    cpu_req,
                "requests.memory": mem_req,
                "limits.cpu":      str(cpu_int * 2),
                "limits.memory":   f"{cpu_int * 4}Gi",
            }
        },
    }

    try:
        core_v1.read_namespaced_resource_quota(name=quota_name, namespace=ns)
        core_v1.replace_namespaced_resource_quota(name=quota_name, namespace=ns, body=body)
        logger.info(f"  Updated ResourceQuota {quota_name} (cpu={cpu_req}, mem={mem_req})")
    except k8s.exceptions.ApiException as e:
        if e.status == 404:
            core_v1.create_namespaced_resource_quota(namespace=ns, body=body)
            logger.info(f"  Created ResourceQuota {quota_name} (cpu={cpu_req}, mem={mem_req})")
        else:
            logger.error(f"  ResourceQuota error: {e}")


def _upsert_network_policy(net_v1, ns, body, logger):
    """Create or replace a NetworkPolicy (idempotent)."""
    name = body["metadata"]["name"]
    try:
        net_v1.read_namespaced_network_policy(name=name, namespace=ns)
        net_v1.replace_namespaced_network_policy(name=name, namespace=ns, body=body)
        logger.info(f"  Updated NetworkPolicy {name}")
    except k8s.exceptions.ApiException as e:
        if e.status == 404:
            net_v1.create_namespaced_network_policy(namespace=ns, body=body)
            logger.info(f"  Created NetworkPolicy {name}")
        else:
            logger.error(f"  NetworkPolicy error for {name}: {e}")


# ---------------------------------------------------------------------------
# CrossfireMitigationIntent handlers
# Nephio acts as the intent engine for the crossfire detection pipeline.
# The pipeline is:
#   sim_detector -> CrossfireDetectionEvent CR
#   -> intent_generator -> CrossfireMitigationIntent CR
#   -> nephio_controller (here) -> NetworkPolicies + HPAs
# ---------------------------------------------------------------------------

CROSSFIRE_MANAGED_LABELS = {
    "crossfire.io/managed-by": "nephio-controller",
    "crossfire.io/managed": "true",
}

# Label selector string for cleanup queries
_CROSSFIRE_LABEL_SEL = "crossfire.io/managed-by=nephio-controller"


def _extract_service_names(pods: list) -> list:
    """
    Derive unique Deployment names from a list of pod metadata dicts.
    Kubernetes Deployment pod names follow: {deployment}-{rs-hash}-{pod-hash}
    Strip the two trailing hash components to recover the deployment name.
    Works for multi-hyphen names (e.g. front-end, orders-db).
    """
    names = set()
    for pod in pods:
        pod_name = pod.get("name", "") if isinstance(pod, dict) else str(pod)
        if not pod_name:
            continue
        parts = pod_name.split("-")
        if len(parts) >= 3:
            names.add("-".join(parts[:-2]))
        else:
            names.add(pod_name)
    return sorted(names)


def _apply_crossfire_network_policies(net_v1, ns, gateways, decoys, logger):
    """
    Isolate crossfire decoy services so only gateway (front-end) traffic
    reaches them.  This prevents the attacker from directly saturating decoy
    pod CPU/network outside the gateway path.
    """
    if not decoys:
        return

    _upsert_network_policy(net_v1, ns, {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {
            "name": "crossfire-decoy-isolation",
            "namespace": ns,
            "labels": CROSSFIRE_MANAGED_LABELS,
            "annotations": {
                "crossfire.io/reason": "crossfire-attack-mitigation",
                "crossfire.io/description": (
                    "Isolates decoy services identified by the crossfire detector. "
                    "Only front-end gateway traffic is permitted."
                ),
            },
        },
        "spec": {
            "podSelector": {
                "matchExpressions": [
                    {"key": "name", "operator": "In", "values": decoys}
                ]
            },
            "policyTypes": ["Ingress"],
            "ingress": [
                {
                    "from": [
                        {
                            "podSelector": {
                                "matchExpressions": [
                                    {"key": "name", "operator": "In", "values": gateways}
                                ]
                            }
                        }
                    ]
                }
            ],
        },
    }, logger)


def _apply_crossfire_hpas(autoscaling_v2, ns, victim_services, decoy_services, logger):
    """
    Create HPAs for detected victim and decoy services.
    - Victims: aggressive scale-up (more replicas = more capacity for degraded traffic)
    - Decoys: moderate scale-up (spread load across more pods, reducing per-pod impact)
    """
    # victim: aggressive — low cpu threshold, fast stabilization
    for svc in victim_services:
        hpa_name = f"crossfire-hpa-{svc}"
        body = {
            "apiVersion": "autoscaling/v2",
            "kind": "HorizontalPodAutoscaler",
            "metadata": {"name": hpa_name, "namespace": ns, "labels": CROSSFIRE_MANAGED_LABELS},
            "spec": {
                "scaleTargetRef": {"apiVersion": "apps/v1", "kind": "Deployment", "name": svc},
                "minReplicas": 2,
                "maxReplicas": 10,
                "metrics": [{
                    "type": "Resource",
                    "resource": {
                        "name": "cpu",
                        "target": {"type": "Utilization", "averageUtilization": 60},
                    },
                }],
                "behavior": {
                    "scaleUp": {
                        "stabilizationWindowSeconds": 10,
                        "policies": [
                            {"type": "Pods", "value": 4, "periodSeconds": 10},
                            {"type": "Percent", "value": 100, "periodSeconds": 10},
                        ],
                        "selectPolicy": "Max",
                    },
                    "scaleDown": {"stabilizationWindowSeconds": 300},
                },
            },
        }
        try:
            autoscaling_v2.read_namespaced_horizontal_pod_autoscaler(name=hpa_name, namespace=ns)
            autoscaling_v2.replace_namespaced_horizontal_pod_autoscaler(name=hpa_name, namespace=ns, body=body)
            logger.info(f"  Updated crossfire HPA {hpa_name} (victim, cpu=60%)")
        except k8s.exceptions.ApiException as e:
            if e.status == 404:
                autoscaling_v2.create_namespaced_horizontal_pod_autoscaler(namespace=ns, body=body)
                logger.info(f"  Created crossfire HPA {hpa_name} (victim, cpu=60%)")
            else:
                logger.error(f"  HPA error for {svc}: {e}")

    # decoy: moderate — spread attack load across more replicas
    for svc in decoy_services:
        hpa_name = f"crossfire-hpa-{svc}"
        body = {
            "apiVersion": "autoscaling/v2",
            "kind": "HorizontalPodAutoscaler",
            "metadata": {"name": hpa_name, "namespace": ns, "labels": CROSSFIRE_MANAGED_LABELS},
            "spec": {
                "scaleTargetRef": {"apiVersion": "apps/v1", "kind": "Deployment", "name": svc},
                "minReplicas": 1,
                "maxReplicas": 5,
                "metrics": [{
                    "type": "Resource",
                    "resource": {
                        "name": "cpu",
                        "target": {"type": "Utilization", "averageUtilization": 80},
                    },
                }],
                "behavior": {
                    "scaleUp": {
                        "stabilizationWindowSeconds": 20,
                        "policies": [
                            {"type": "Pods", "value": 2, "periodSeconds": 15},
                        ],
                        "selectPolicy": "Max",
                    },
                    "scaleDown": {"stabilizationWindowSeconds": 300},
                },
            },
        }
        try:
            autoscaling_v2.read_namespaced_horizontal_pod_autoscaler(name=hpa_name, namespace=ns)
            autoscaling_v2.replace_namespaced_horizontal_pod_autoscaler(name=hpa_name, namespace=ns, body=body)
            logger.info(f"  Updated crossfire HPA {hpa_name} (decoy, cpu=80%)")
        except k8s.exceptions.ApiException as e:
            if e.status == 404:
                autoscaling_v2.create_namespaced_horizontal_pod_autoscaler(namespace=ns, body=body)
                logger.info(f"  Created crossfire HPA {hpa_name} (decoy, cpu=80%)")
            else:
                logger.error(f"  HPA error for {svc}: {e}")


@kopf.on.create("crossfire.io", "v1alpha1", "crossfiremitigationintents")
@kopf.on.update("crossfire.io", "v1alpha1", "crossfiremitigationintents")
def reconcile_crossfire_intent(spec, name, namespace, logger, **kwargs):
    """
    Reconcile a CrossfireMitigationIntent into concrete K8s resources.
    Replaces the custom controller.py with Nephio as the intent engine.
    """
    kubernetes.config.load_kube_config()
    net_v1 = k8s.NetworkingV1Api()
    autoscaling_v2 = k8s.AutoscalingV2Api()

    decoy_pods = spec.get("decoyPods", [])
    victim_pods = spec.get("victimPods", [])

    # Derive target namespace from the first pod entry
    target_ns = (
        decoy_pods[0].get("namespace") if decoy_pods else
        victim_pods[0].get("namespace") if victim_pods else
        "sock-shop"
    )

    decoy_services = _extract_service_names(decoy_pods)
    victim_services = _extract_service_names(victim_pods)
    gateways = ["front-end"]

    logger.info(
        f"Nephio reconciling CrossfireMitigationIntent '{name}': "
        f"ns={target_ns}, decoys={decoy_services}, victims={victim_services}"
    )

    _apply_crossfire_network_policies(net_v1, target_ns, gateways, decoy_services, logger)
    _apply_crossfire_hpas(autoscaling_v2, target_ns, victim_services, decoy_services, logger)

    logger.info(f"Nephio reconciliation complete for CrossfireMitigationIntent '{name}'")
    return {
        "nephio_reconciled": True,
        "target_ns": target_ns,
        "decoy_services": decoy_services,
        "victim_services": victim_services,
    }


@kopf.on.delete("crossfire.io", "v1alpha1", "crossfiremitigationintents")
def cleanup_crossfire_intent(spec, name, namespace, logger, **kwargs):
    """Remove all K8s resources created for a CrossfireMitigationIntent."""
    kubernetes.config.load_kube_config()
    net_v1 = k8s.NetworkingV1Api()
    autoscaling_v2 = k8s.AutoscalingV2Api()

    decoy_pods = spec.get("decoyPods", [])
    victim_pods = spec.get("victimPods", [])
    target_ns = (
        decoy_pods[0].get("namespace") if decoy_pods else
        victim_pods[0].get("namespace") if victim_pods else
        "sock-shop"
    )

    logger.info(f"Nephio cleanup for CrossfireMitigationIntent '{name}' in {target_ns}")

    # Remove NetworkPolicies tagged with crossfire.io/managed-by=nephio-controller
    try:
        net_v1.delete_collection_namespaced_network_policy(
            namespace=target_ns, label_selector=_CROSSFIRE_LABEL_SEL
        )
        logger.info(f"  Removed crossfire NetworkPolicies from {target_ns}")
    except Exception as e:
        logger.warning(f"  NP cleanup error: {e}")

    # Remove HPAs by name
    all_services = _extract_service_names(decoy_pods) + _extract_service_names(victim_pods)
    for svc in all_services:
        hpa_name = f"crossfire-hpa-{svc}"
        try:
            autoscaling_v2.delete_namespaced_horizontal_pod_autoscaler(
                name=hpa_name, namespace=target_ns
            )
            logger.info(f"  Removed HPA {hpa_name}")
        except k8s.exceptions.ApiException as e:
            if e.status != 404:
                logger.warning(f"  HPA cleanup error for {hpa_name}: {e}")

    logger.info(f"Nephio cleanup complete for '{name}'")


@kopf.daemon(
    "crossfire.io", "v1alpha1", "crossfiremitigationintents",
    cancellation_timeout=5,
)
async def ttl_daemon(spec, name, logger, stopped, **kwargs):
    """
    Auto-delete the CrossfireMitigationIntent when its TTL expires.
    Deletion triggers cleanup_crossfire_intent to remove generated resources.
    """
    import asyncio as _asyncio
    ttl = int(spec.get("ttlSeconds", 300))
    logger.info(f"TTL daemon started for CMI '{name}', expires in {ttl}s")
    try:
        await _asyncio.wait_for(stopped.wait(), timeout=float(ttl))
        logger.info(f"CMI '{name}' deleted before TTL — daemon stopping")
    except _asyncio.TimeoutError:
        logger.info(f"TTL expired for CMI '{name}', triggering auto-deletion")
        try:
            kubernetes.config.load_kube_config()
            custom_api = k8s.CustomObjectsApi()
            custom_api.delete_cluster_custom_object(
                "crossfire.io", "v1alpha1", "crossfiremitigationintents", name
            )
        except Exception as e:
            logger.warning(f"TTL auto-delete failed for '{name}': {e}")
