"""
IntentGenerator — watches CrossfireDetectionEvent CRs and creates
CrossfireMitigationIntent CRs.

Design principles
-----------------
- Watches K8s API for CrossfireDetectionEvent objects.
- Translates each unresolved event into a CrossfireMitigationIntent.
- Selects mitigation strategy based on confidence score and cluster capacity.
- Does NOT implement any mitigation itself — pure translation layer.
- Exports Prometheus metrics.
- Idempotent: skips events that already have a mitigationRef in status.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import aiohttp
from prometheus_client import Counter, Gauge, start_http_server

logging.basicConfig(
    level=logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s","msg":"%(message)s"}',
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
log = logging.getLogger("crossfire-intent-generator")

# ---------------------------------------------------------------------------
# Self-observability metrics
# ---------------------------------------------------------------------------
INTENTS_CREATED = Counter(
    "crossfire_intents_created_total",
    "Total CrossfireMitigationIntents created",
    ["strategy"],
)
INTENTS_SKIPPED = Counter(
    "crossfire_intents_skipped_total",
    "Events skipped (already have mitigationRef or resolved)",
    ["reason"],
)
WATCH_RECONNECTS = Counter(
    "crossfire_watch_reconnects_total",
    "Number of times the K8s watch stream reconnected",
)
ACTIVE_INTENTS = Gauge(
    "crossfire_active_intents",
    "Number of currently active CrossfireMitigationIntents",
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
@dataclass
class GeneratorConfig:
    k8s_api_url: str = field(
        default_factory=lambda: os.getenv(
            "KUBERNETES_API_URL", "https://kubernetes.default.svc"
        )
    )
    crd_group: str = "crossfire.io"
    crd_version: str = "v1alpha1"
    event_plural: str = "crossfiredetectionevents"
    intent_plural: str = "crossfiremitigationintents"

    # Strategy selection thresholds
    high_confidence_threshold: float = field(
        default_factory=lambda: float(os.getenv("HIGH_CONFIDENCE_THRESHOLD", "0.85"))
    )
    # Default TTL for generated intents (seconds)
    default_ttl_seconds: int = field(
        default_factory=lambda: int(os.getenv("INTENT_TTL_SECONDS", "300"))
    )
    # Bandwidth caps applied by BandwidthConstraint strategy
    decoy_egress_limit_mbps: int = field(
        default_factory=lambda: int(os.getenv("DECOY_EGRESS_LIMIT_MBPS", "100"))
    )
    decoy_ingress_limit_mbps: int = field(
        default_factory=lambda: int(os.getenv("DECOY_INGRESS_LIMIT_MBPS", "100"))
    )
    # Priority value for elevated victim PriorityClass
    victim_priority_value: int = field(
        default_factory=lambda: int(os.getenv("VICTIM_PRIORITY_VALUE", "1000000"))
    )
    metrics_port: int = field(
        default_factory=lambda: int(os.getenv("METRICS_PORT", "8081"))
    )


# ---------------------------------------------------------------------------
# K8s client (minimal — same pattern as detector)
# ---------------------------------------------------------------------------
class K8sClient:
    def __init__(self, session: aiohttp.ClientSession, api: str, token: str, ca: str):
        self._session = session
        self._api = api.rstrip("/")
        self._token = token
        self._ca = ca
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    @property
    def _ssl(self) -> Any:
        if self._ca and os.path.exists(self._ca):
            import ssl
            ctx = ssl.create_default_context(cafile=self._ca)
            return ctx
        return False

    async def watch_cluster_resources(
        self, group: str, version: str, plural: str, resource_version: str = ""
    ):
        """
        Async generator that yields (event_type, object) tuples
        from a K8s watch stream.
        Uses chunked transfer encoding from the K8s API.
        """
        url = f"{self._api}/apis/{group}/{version}/{plural}"
        params = {"watch": "true", "timeoutSeconds": "300"}
        if resource_version:
            params["resourceVersion"] = resource_version

        async with self._session.get(
            url,
            params=params,
            headers=self._headers,
            ssl=self._ssl,
            timeout=aiohttp.ClientTimeout(total=None, sock_read=310),
        ) as resp:
            if resp.status != 200:
                log.warning(f"Watch returned HTTP {resp.status}")
                return
            async for raw_line in resp.content:
                line = raw_line.decode("utf-8").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    yield event.get("type", ""), event.get("object", {})
                except json.JSONDecodeError:
                    log.warning(f"Failed to parse watch event: {line[:100]}")

    async def get_cluster_resource(
        self, group: str, version: str, plural: str, name: str
    ) -> Optional[Dict]:
        url = f"{self._api}/apis/{group}/{version}/{plural}/{name}"
        try:
            async with self._session.get(
                url,
                headers=self._headers,
                ssl=self._ssl,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                return None
        except Exception as e:
            log.warning(f"GET {url}: {e}")
            return None

    async def create_cluster_resource(
        self, group: str, version: str, plural: str, body: Dict
    ) -> Optional[Dict]:
        url = f"{self._api}/apis/{group}/{version}/{plural}"
        try:
            async with self._session.post(
                url,
                json=body,
                headers=self._headers,
                ssl=self._ssl,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json()
                if resp.status in (200, 201):
                    return data
                if resp.status == 409:
                    log.info(f"Intent already exists: {body['metadata']['name']}")
                    return data
                log.warning(f"CREATE failed {resp.status}: {str(data)[:200]}")
                return None
        except Exception as e:
            log.warning(f"CREATE error: {e}")
            return None

    async def patch_resource_status(
        self, group: str, version: str, plural: str, name: str, status: Dict
    ) -> Optional[Dict]:
        url = f"{self._api}/apis/{group}/{version}/{plural}/{name}/status"
        patch = {"status": status}
        headers = {**self._headers, "Content-Type": "application/merge-patch+json"}
        try:
            async with self._session.patch(
                url,
                json=patch,
                headers=headers,
                ssl=self._ssl,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status in (200, 201):
                    return await resp.json()
                log.warning(f"PATCH status {resp.status}")
                return None
        except Exception as e:
            log.warning(f"PATCH error: {e}")
            return None


# ---------------------------------------------------------------------------
# Strategy selection logic
# ---------------------------------------------------------------------------
def select_strategy(event_spec: Dict, config: GeneratorConfig) -> Dict:
    """
    Determine the mitigation strategy based on the detection event spec.

    Rules:
    - confidence >= HIGH_CONFIDENCE_THRESHOLD → Hybrid (all three strategies)
    - confidence < HIGH_CONFIDENCE_THRESHOLD → BandwidthConstraint only
      (conservative: only throttle decoys, don't disrupt topology)
    - If no deploymentName on victim pods → skip TopologyEviction (can't patch Deployment)

    Returns the strategy dict for the CrossfireMitigationIntent spec.
    """
    confidence = event_spec.get("confidenceScore", 0.0)
    victim_pods = event_spec.get("victimPods", [])
    victims_have_deployments = any(p.get("deploymentName") for p in victim_pods)

    if confidence >= config.high_confidence_threshold and victims_have_deployments:
        strategy_type = "Hybrid"
    elif confidence >= config.high_confidence_threshold:
        strategy_type = "BandwidthConstraint"
    else:
        strategy_type = "BandwidthConstraint"

    return {
        "type": strategy_type,
        "decoyEgressLimitMbps": config.decoy_egress_limit_mbps,
        "decoyIngressLimitMbps": config.decoy_ingress_limit_mbps,
        "victimPriorityValue": config.victim_priority_value,
        "victimPriorityClassName": "crossfire-victim-priority",
        "evictDecoysFromNode": True,
        "decoyMaxReplicas": 0,
        "victimScaleUpReplicas": 3,
    }


# ---------------------------------------------------------------------------
# Intent generator
# ---------------------------------------------------------------------------
class IntentGenerator:
    def __init__(self, config: GeneratorConfig):
        self._config = config

    async def run_forever(self) -> None:
        token = _read_sa_token()
        ca = _read_ca_cert()
        start_http_server(self._config.metrics_port)
        log.info(f"IntentGenerator starting, metrics on :{self._config.metrics_port}")

        while True:
            try:
                await self._watch_loop(token, ca)
            except Exception as exc:
                log.error(f"Watch loop error: {exc}; reconnecting in 10s")
                WATCH_RECONNECTS.inc()
                await asyncio.sleep(10)

    async def _watch_loop(self, token: str, ca: str) -> None:
        async with aiohttp.ClientSession() as session:
            k8s = K8sClient(session, self._config.k8s_api_url, token, ca)
            cfg = self._config

            log.info("Starting watch on CrossfireDetectionEvent resources")
            WATCH_RECONNECTS.inc()

            async for event_type, obj in k8s.watch_cluster_resources(
                cfg.crd_group, cfg.crd_version, cfg.event_plural
            ):
                if event_type not in ("ADDED", "MODIFIED"):
                    continue

                spec = obj.get("spec", {})
                status = obj.get("status", {})
                name = obj.get("metadata", {}).get("name", "")

                # Skip resolved events
                if status.get("resolved"):
                    INTENTS_SKIPPED.labels(reason="already_resolved").inc()
                    continue

                # Skip events that already have a mitigationRef
                if status.get("mitigationRef"):
                    INTENTS_SKIPPED.labels(reason="already_has_intent").inc()
                    continue

                log.info(f"Processing DetectionEvent {name} (confidence={spec.get('confidenceScore')})")
                await self._process_event(k8s, name, spec)

    async def _process_event(
        self, k8s: K8sClient, event_name: str, spec: Dict
    ) -> None:
        cfg = self._config
        intent_name = f"cfmi-{event_name}"

        strategy = select_strategy(spec, cfg)

        intent_body = {
            "apiVersion": f"{cfg.crd_group}/{cfg.crd_version}",
            "kind": "CrossfireMitigationIntent",
            "metadata": {
                "name": intent_name,
                "labels": {
                    "crossfire.io/trigger-event": event_name,
                    "crossfire.io/node": spec.get("affectedNode", ""),
                    "app.kubernetes.io/managed-by": "crossfire-intent-generator",
                },
            },
            "spec": {
                "triggerEventRef": event_name,
                "affectedNode": spec.get("affectedNode", ""),
                "congestedInterface": spec.get("congestedInterface", ""),
                "decoyPods": spec.get("decoyPods", []),
                "victimPods": spec.get("victimPods", []),
                "strategy": strategy,
                "ttlSeconds": cfg.default_ttl_seconds,
            },
        }

        result = await k8s.create_cluster_resource(
            cfg.crd_group, cfg.crd_version, cfg.intent_plural, intent_body
        )

        if result and "metadata" in result:
            intent_name_actual = result["metadata"]["name"]
            log.info(f"Created CrossfireMitigationIntent {intent_name_actual} for event {event_name}")
            INTENTS_CREATED.labels(strategy=strategy["type"]).inc()
            ACTIVE_INTENTS.inc()

            # Update the detection event status with the mitigationRef
            await k8s.patch_resource_status(
                cfg.crd_group,
                cfg.crd_version,
                cfg.event_plural,
                event_name,
                {"mitigationRef": intent_name_actual},
            )
        else:
            log.error(f"Failed to create intent for event {event_name}")


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
    config = GeneratorConfig()
    gen = IntentGenerator(config)
    await gen.run_forever()


if __name__ == "__main__":
    asyncio.run(_main())
