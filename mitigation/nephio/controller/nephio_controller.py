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
