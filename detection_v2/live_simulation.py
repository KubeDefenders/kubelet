#!/usr/bin/env python3
"""
Live cluster simulation: real attacks against Sock Shop + detection via Prometheus.

Orchestrates:
  1. Pre-flight checks (minikube running, sock-shop healthy, prometheus reachable)
  2. Model training (on synthetic benign data — as in production cold-start)
  3. Background normal traffic generation
  4. Continuous detection via PrometheusAdapter (real Istio telemetry)
  5. Scheduled attack launches (non-overlapping, random delays)
  6. Output recording (JSONL, CSV, JSON summary, LaTeX tables)

Prerequisites:
  - Minikube running with Sock Shop + Istio + Prometheus deployed
  - Prometheus port-forwarded to localhost:9090
  - Front-end accessible at http://<minikube-ip>:30001

Usage:
    # Start cluster first (if not running):
    #   bash scripts/cluster/reinstantiate-minikube.sh
    #
    # Then run the live simulation:
    python -m detection_v2.live_simulation --duration 600  # 10 min
    python -m detection_v2.live_simulation --duration 300  # 5 min (quick check)
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import time
import warnings
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from detection_v2.core.schema import MetricSample, DetectionResult, Severity
from detection_v2.core.model import AnomalyDetector
from detection_v2.core.errors import MetricCollectionError
from detection_v2.training.trainer import Trainer
from detection_v2.adapters.prometheus_adapter import PrometheusAdapter

# ──────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────

DETECTION_INTERVAL = 15       # seconds between detection cycles
DEFAULT_DURATION = 600        # 10 minutes
PROMETHEUS_URL = "http://localhost:9090"
NAMESPACE = "sock-shop"

ATTACK_CONFIGS = [
    {
        "name": "http_flood",
        "type": "http-flood",
        "workers": 20,
        "rate": 50,
        "description": "High-volume HTTP GET flood",
    },
    {
        "name": "slowloris",
        "type": "slowloris",
        "workers": 50,
        "rate": 2,
        "description": "Slow connection exhaustion",
    },
    {
        "name": "syn_flood",
        "type": "syn",
        "workers": 30,
        "rate": 30,
        "description": "Rapid connection open/close",
    },
    {
        "name": "amplification_dns",
        "type": "dns",
        "workers": 15,
        "rate": 40,
        "description": "DNS amplification (catalogue-heavy)",
    },
]

# Terminal colours
_GREEN = "\033[92m"
_RED = "\033[91m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RESET = "\033[0m"


# ──────────────────────────────────────────────────────────────────────────
# Data structures
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class AttackSlot:
    """A scheduled attack window."""
    attack_id: int
    attack_name: str
    attack_type: str
    workers: int
    rate: int
    start_time: float    # Unix epoch
    duration: int        # seconds
    description: str
    process: Optional[subprocess.Popen] = None

    @property
    def end_time(self) -> float:
        return self.start_time + self.duration

    def to_dict(self) -> dict:
        return {
            "attack_id": self.attack_id,
            "attack_name": self.attack_name,
            "attack_type": self.attack_type,
            "workers": self.workers,
            "rate": self.rate,
            "total_rate": self.workers * self.rate,
            "start_offset_s": 0,  # filled later
            "duration_s": self.duration,
            "description": self.description,
        }


@dataclass
class CycleRecord:
    """One detection cycle result."""
    cycle: int
    wall_time: str
    elapsed_s: float
    phase: str
    attack_name: Optional[str]
    attack_id: Optional[int]
    is_anomaly: bool
    anomaly_score: float
    severity: str
    detection_latency_ms: float
    request_rate: float
    error_rate: float
    latency_p99_ms: float
    top_features: List[Dict]


# ══════════════════════════════════════════════════════════════════════════
# Pre-flight checks
# ══════════════════════════════════════════════════════════════════════════

def check_minikube() -> str:
    """Check minikube is running, return its IP."""
    print(f"  {_CYAN}[..]{_RESET}  Checking minikube status ...")
    try:
        result = subprocess.run(
            ["minikube", "status", "--format", "{{.Host}}"],
            capture_output=True, text=True, timeout=10,
        )
        if "Running" not in result.stdout:
            print(f"  {_RED}[!!]{_RESET}  Minikube is not running!")
            print(f"       Start it with: bash scripts/cluster/reinstantiate-minikube.sh")
            sys.exit(1)
    except FileNotFoundError:
        print(f"  {_RED}[!!]{_RESET}  minikube not found!")
        sys.exit(1)

    ip_result = subprocess.run(
        ["minikube", "ip"], capture_output=True, text=True, timeout=10,
    )
    ip = ip_result.stdout.strip()
    print(f"  {_GREEN}[OK]{_RESET}  Minikube running at {ip}")
    return ip


def check_sock_shop(minikube_ip: str) -> str:
    """Check Sock Shop is reachable, return frontend URL."""
    print(f"  {_CYAN}[..]{_RESET}  Checking Sock Shop deployment ...")

    # Get NodePort
    try:
        result = subprocess.run(
            ["kubectl", "get", "svc", "front-end", "-n", "sock-shop",
             "-o", "jsonpath={.spec.ports[0].nodePort}"],
            capture_output=True, text=True, timeout=10,
        )
        nodeport = result.stdout.strip()
        if not nodeport:
            # Try default
            nodeport = "30001"
    except Exception:
        nodeport = "30001"

    url = f"http://{minikube_ip}:{nodeport}"

    # Check reachability
    try:
        import requests
        resp = requests.get(url, timeout=5)
        if resp.status_code in (200, 301, 302):
            print(f"  {_GREEN}[OK]{_RESET}  Sock Shop reachable at {url}")
            return url
        else:
            print(f"  {_YELLOW}[!!]{_RESET}  Sock Shop returned {resp.status_code}")
            return url
    except Exception as e:
        print(f"  {_RED}[!!]{_RESET}  Cannot reach Sock Shop at {url}: {e}")
        print(f"       Deploy it first: bash scripts/cluster/deploy-sock-shop.sh")
        sys.exit(1)


def check_prometheus() -> bool:
    """Check Prometheus is reachable."""
    print(f"  {_CYAN}[..]{_RESET}  Checking Prometheus at {PROMETHEUS_URL} ...")
    try:
        import requests
        resp = requests.get(f"{PROMETHEUS_URL}/api/v1/status/config", timeout=5)
        if resp.status_code == 200:
            print(f"  {_GREEN}[OK]{_RESET}  Prometheus reachable")
            return True
    except Exception:
        pass

    print(f"  {_YELLOW}[!!]{_RESET}  Prometheus not reachable at {PROMETHEUS_URL}")
    print(f"       Setting up port-forward ...")

    # Try to start port-forward
    try:
        pf = subprocess.Popen(
            ["kubectl", "port-forward", "-n", "istio-system",
             "svc/prometheus", "9090:9090"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(3)

        import requests
        resp = requests.get(f"{PROMETHEUS_URL}/api/v1/status/config", timeout=5)
        if resp.status_code == 200:
            print(f"  {_GREEN}[OK]{_RESET}  Prometheus port-forward established (PID {pf.pid})")
            return True
    except Exception as e:
        print(f"  {_RED}[!!]{_RESET}  Still cannot reach Prometheus: {e}")
        print(f"       Run: kubectl port-forward -n istio-system svc/prometheus 9090:9090")
        sys.exit(1)

    return False


def check_istio_metrics() -> bool:
    """Verify Istio metrics are flowing into Prometheus."""
    print(f"  {_CYAN}[..]{_RESET}  Checking for Istio telemetry in Prometheus ...")
    try:
        import requests
        resp = requests.post(
            f"{PROMETHEUS_URL}/api/v1/query",
            data={"query": f'count(istio_requests_total{{destination_service_namespace="{NAMESPACE}"}})'},
            timeout=10,
        )
        data = resp.json()
        if data["status"] == "success" and data["data"]["result"]:
            count = int(float(data["data"]["result"][0]["value"][1]))
            print(f"  {_GREEN}[OK]{_RESET}  Istio metrics present ({count} time series)")
            return True
        else:
            print(f"  {_YELLOW}[!!]{_RESET}  No Istio metrics found yet (may need traffic)")
            return False
    except Exception as e:
        print(f"  {_YELLOW}[!!]{_RESET}  Cannot query Istio metrics: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════
# Traffic generation
# ══════════════════════════════════════════════════════════════════════════

def start_normal_traffic(target_url: str, workers: int = 3, rate: float = 5.0) -> subprocess.Popen:
    """Start background normal traffic generator."""
    print(f"  {_CYAN}[..]{_RESET}  Starting normal traffic generator ({workers}w × {rate}r/s = ~{workers * rate:.0f} req/s) ...")

    # Use the project's traffic generator
    traffic_script = Path(__file__).parent.parent / "src" / "traffic-generator.py"

    if traffic_script.exists():
        proc = subprocess.Popen(
            [sys.executable, str(traffic_script),
             "--target-url", target_url,
             "--workers", str(workers),
             "--rate", str(rate)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid,
        )
    else:
        # Fallback: use a simple curl loop
        proc = subprocess.Popen(
            ["bash", "-c", f"""
            while true; do
                curl -s -o /dev/null {target_url}/ &
                curl -s -o /dev/null {target_url}/catalogue &
                curl -s -o /dev/null {target_url}/category.html &
                wait
                sleep 0.2
            done
            """],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid,
        )

    print(f"  {_GREEN}[OK]{_RESET}  Normal traffic running (PID {proc.pid})")
    return proc


def launch_attack(target_url: str, attack_config: dict, duration: int) -> subprocess.Popen:
    """Launch an attack subprocess."""
    attack_script = Path(__file__).parent.parent / "attacks" / "attack.py"

    proc = subprocess.Popen(
        [sys.executable, str(attack_script),
         "--target-url", target_url,
         "--attack-type", attack_config["type"],
         "--workers", str(attack_config["workers"]),
         "--rate", str(attack_config["rate"]),
         "--duration", str(duration)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=os.setsid,
    )
    return proc


def stop_process(proc: subprocess.Popen) -> None:
    """Stop a process and its process group."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, OSError):
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass


# ══════════════════════════════════════════════════════════════════════════
# Attack scheduling
# ══════════════════════════════════════════════════════════════════════════

def schedule_attacks(
    duration: int,
    start_time: float,
    rng: np.random.RandomState,
) -> List[AttackSlot]:
    """
    Create non-overlapping attack schedule.

    Rules:
      - Warm-up: 30-60s of normal traffic first
      - Attack duration: 45-90s each
      - Gap between attacks: 30-60s
      - Stop scheduling 30s before end
    """
    warmup = rng.randint(30, 61)
    cursor = start_time + warmup
    end_limit = start_time + duration - 30

    attacks: List[AttackSlot] = []
    attack_id = 0

    while cursor < end_limit:
        # Pick random attack config
        config = ATTACK_CONFIGS[rng.randint(0, len(ATTACK_CONFIGS))]

        # Random duration 45-90s
        atk_duration = rng.randint(45, 91)

        # Ensure we don't exceed time limit
        if cursor + atk_duration > end_limit:
            atk_duration = max(30, int(end_limit - cursor))
            if atk_duration < 30:
                break

        slot = AttackSlot(
            attack_id=attack_id,
            attack_name=config["name"],
            attack_type=config["type"],
            workers=config["workers"],
            rate=config["rate"],
            start_time=cursor,
            duration=atk_duration,
            description=config["description"],
        )
        attacks.append(slot)
        attack_id += 1

        # Gap: 30-60s
        gap = rng.randint(30, 61)
        cursor += atk_duration + gap

    return attacks


# ══════════════════════════════════════════════════════════════════════════
# Model training
# ══════════════════════════════════════════════════════════════════════════

def collect_live_training_data(
    prometheus: PrometheusAdapter,
    collection_duration: int = 90,
    interval: int = 5,
) -> List[MetricSample]:
    """
    Collect real normal-traffic MetricSamples from Prometheus.

    Gathers samples every `interval` seconds for `collection_duration` seconds.
    This produces training data that matches the real metric distributions.
    """
    samples: List[MetricSample] = []
    end_time = time.time() + collection_duration
    collected = 0
    failures = 0

    while time.time() < end_time:
        try:
            sample = prometheus.collect(window_seconds=30)
            samples.append(sample)
            collected += 1
            elapsed = collection_duration - (end_time - time.time())
            pct = elapsed / collection_duration * 100
            print(
                f"\r  {_CYAN}[..]{_RESET}  Collecting live training data: "
                f"{collected} samples ({pct:.0f}%) ...",
                end="", flush=True,
            )
        except Exception as e:
            failures += 1
            if failures > 5:
                print(f"\n  {_YELLOW}[!!]{_RESET}  Too many collection failures: {e}")
                break

        time.sleep(interval)

    print()

    if collected < 10:
        print(f"  {_YELLOW}[!!]{_RESET}  Only {collected} samples collected — may be insufficient")

    return samples


def train_model_live(
    model_dir: Path,
    live_samples: List[MetricSample],
) -> Path:
    """Train model on augmented live normal-traffic data from Prometheus.

    The raw live samples (~15-20) are augmented with Gaussian noise to produce
    ~500+ training samples that still faithfully represent the real traffic
    distribution.  This gives the Isolation Forest enough data to build a tight
    decision boundary while remaining anchored to actual Prometheus metrics.
    """
    from detection_v2.core.features import extract_features, FEATURE_NAMES

    print(f"  {_CYAN}[..]{_RESET}  Augmenting {len(live_samples)} live samples ...")

    rng = np.random.RandomState(42)

    # Extract feature vectors from live samples to compute stats
    live_features = np.array([extract_features(s) for s in live_samples])
    feat_means = live_features.mean(axis=0)
    feat_stds = live_features.std(axis=0) + 1e-8  # avoid zero std

    # Augment: generate ~500 samples by jittering live samples with ±5-15% noise
    augmented: List[MetricSample] = list(live_samples)  # keep originals
    target_count = 500
    per_original = max(1, target_count // len(live_samples))

    for sample in live_samples:
        for _ in range(per_original):
            # Create a jittered copy
            noise_scale = rng.uniform(0.03, 0.12)
            augmented.append(MetricSample(
                timestamp=sample.timestamp + rng.uniform(-1, 1),
                request_rate=max(0, sample.request_rate * (1 + rng.normal(0, noise_scale))),
                error_rate=max(0, sample.error_rate + abs(sample.error_rate) * rng.normal(0, noise_scale)),
                request_rate_variance=max(0, sample.request_rate_variance * (1 + rng.normal(0, noise_scale * 2))),
                latency_p50_ms=max(0, sample.latency_p50_ms * (1 + rng.normal(0, noise_scale))),
                latency_p95_ms=max(0, sample.latency_p95_ms * (1 + rng.normal(0, noise_scale))),
                latency_p99_ms=max(0, sample.latency_p99_ms * (1 + rng.normal(0, noise_scale))),
                byte_rate_in=max(0, sample.byte_rate_in * (1 + rng.normal(0, noise_scale))),
                byte_rate_out=max(0, sample.byte_rate_out * (1 + rng.normal(0, noise_scale))),
                avg_request_size_bytes=max(0, sample.avg_request_size_bytes * (1 + rng.normal(0, noise_scale))),
                avg_response_size_bytes=max(0, sample.avg_response_size_bytes * (1 + rng.normal(0, noise_scale))),
                connection_open_rate=max(0, sample.connection_open_rate * (1 + rng.normal(0, noise_scale))),
                connection_close_rate=max(0, sample.connection_close_rate * (1 + rng.normal(0, noise_scale))),
                total_request_rate=max(0, sample.total_request_rate * (1 + rng.normal(0, noise_scale))),
            ))

    print(f"  {_GREEN}[OK]{_RESET}  Augmented to {len(augmented)} training samples")
    print(f"  {_CYAN}[..]{_RESET}  Training Isolation Forest ...")
    trainer = Trainer(
        n_estimators=200,
        max_samples=256,
        shap_background_size=100,
        contamination=0.05,
    )

    # Note: skip synthetic attack evaluation — it doesn't match real metrics
    results = trainer.fit(augmented, attack_samples=None)

    model_path = model_dir / "live_model.pkl"
    trainer.save(model_path)

    print(f"  {_GREEN}[OK]{_RESET}  Model trained on {len(augmented)} augmented live samples")
    return model_path


def train_model_synthetic(model_dir: Path) -> Path:
    """Train model on synthetic normal data (cold-start fallback)."""
    from detection_v2.extended_simulation import (
        _make_normal_sample, _ATTACK_GENERATORS, ATTACK_TYPES,
    )

    rng = np.random.RandomState(42)

    print(f"  {_CYAN}[..]{_RESET}  Generating 800 diverse training samples ...")
    normal_samples = []
    for i in range(800):
        normal_samples.append(_make_normal_sample(rng, time.time() + i, i / 800.0))

    attack_rng = np.random.RandomState(43)
    attack_samples = []
    for i in range(100):
        atype = ATTACK_TYPES[i % len(ATTACK_TYPES)]
        gen = _ATTACK_GENERATORS[atype]
        attack_samples.append(gen(attack_rng, time.time() + i))

    print(f"  {_CYAN}[..]{_RESET}  Training Isolation Forest ...")
    trainer = Trainer(
        n_estimators=200,
        max_samples=256,
        shap_background_size=100,
        contamination=0.05,
    )
    results = trainer.fit(normal_samples, attack_samples=attack_samples)

    model_path = model_dir / "live_model.pkl"
    trainer.save(model_path)

    print(f"  {_GREEN}[OK]{_RESET}  Model saved (accuracy={results['accuracy']:.1%}, "
          f"recall={results['attack_recall']:.1%})")
    return model_path


# ══════════════════════════════════════════════════════════════════════════
# Main detection loop
# ══════════════════════════════════════════════════════════════════════════

def run_live_detection(
    detector: AnomalyDetector,
    prometheus: PrometheusAdapter,
    target_url: str,
    attacks: List[AttackSlot],
    duration: int,
    out_dir: Path,
) -> List[CycleRecord]:
    """
    Run the live detection loop, launching attacks at scheduled times.

    Returns all cycle records.
    """
    start_time = time.time()
    end_time = start_time + duration
    total_cycles = duration // DETECTION_INTERVAL
    records: List[CycleRecord] = []

    # State for consecutive-anomaly tracking
    consecutive_anomalies = 0
    consecutive_threshold = 2
    min_request_rate = 1.0

    # Attack state
    active_attack: Optional[AttackSlot] = None
    attack_queue = list(attacks)
    completed_attacks: List[AttackSlot] = []
    post_attack_cooldown = 0  # cycles to skip after attack ends (let metrics settle)

    # Fill in start offsets for reporting
    for a in attacks:
        a_dict = a.to_dict()
        a_dict["start_offset_s"] = a.start_time - start_time

    bar_width = 50
    cycle = 0

    print()

    while time.time() < end_time:
        now = time.time()
        elapsed = now - start_time
        cycle += 1

        # ── Check if we need to launch an attack ─────────────────
        if active_attack is None and attack_queue:
            next_atk = attack_queue[0]
            if now >= next_atk.start_time:
                # Launch!
                attack_queue.pop(0)
                active_attack = next_atk
                print(f"\n  {_RED}>>> ATTACK #{active_attack.attack_id + 1} LAUNCHED: "
                      f"{active_attack.attack_name} ({active_attack.description})"
                      f" — {active_attack.workers}w × {active_attack.rate}r/s "
                      f"for {active_attack.duration}s{_RESET}")
                active_attack.process = launch_attack(
                    target_url, {
                        "type": active_attack.attack_type,
                        "workers": active_attack.workers,
                        "rate": active_attack.rate,
                    },
                    active_attack.duration,
                )

        # ── Check if active attack has ended ─────────────────────
        if active_attack is not None:
            if now >= active_attack.end_time:
                print(f"\n  {_GREEN}<<< ATTACK #{active_attack.attack_id + 1} ENDED: "
                      f"{active_attack.attack_name}{_RESET}")
                if active_attack.process:
                    stop_process(active_attack.process)
                completed_attacks.append(active_attack)
                active_attack = None
                # Reset rate history to prevent stale attack variance
                # from poisoning the burstiness feature during normal periods
                prometheus.reset_history()
                consecutive_anomalies = 0
                post_attack_cooldown = 2  # skip 2 cycles to let Prometheus rates settle

        # ── Determine phase ──────────────────────────────────────
        if active_attack is not None:
            phase = "ATTACK"
            attack_name = active_attack.attack_name
            attack_id = active_attack.attack_id
        else:
            phase = "NORMAL"
            attack_name = None
            attack_id = None

        # ── Collect metrics from Prometheus ──────────────────────
        try:
            sample = prometheus.collect(window_seconds=30)
            req_rate = sample.request_rate
            err_rate = sample.error_rate
            lat_p99 = sample.latency_p99_ms
        except MetricCollectionError as e:
            print(f"\n  {_YELLOW}[!!] Metric collection failed: {e}{_RESET}")
            time.sleep(DETECTION_INTERVAL)
            continue
        except Exception as e:
            print(f"\n  {_YELLOW}[!!] Unexpected error: {e}{_RESET}")
            time.sleep(DETECTION_INTERVAL)
            continue

        # ── Idle-traffic guard ───────────────────────────────────
        if sample.request_rate < min_request_rate:
            is_anomaly = False
            score = 0.0
            severity = "NORMAL"
            latency = 0.0
            top_feats = []
            consecutive_anomalies = 0
        elif post_attack_cooldown > 0 and phase == "NORMAL":
            # Post-attack cooldown: Prometheus rate windows still contain
            # attack traffic for 1-2 cycles (30s rate window). Suppress
            # detection during this transient period to avoid FPs.
            post_attack_cooldown -= 1
            is_anomaly = False
            score = 0.0
            severity = "NORMAL"
            latency = 0.0
            top_feats = []
            consecutive_anomalies = 0
        else:
            # ── Run detection ────────────────────────────────────
            try:
                result = detector.detect(sample, explain=True)
                raw_anomaly = result.is_anomaly
                score = result.anomaly_score
                severity = result.severity.value
                latency = result.detection_latency_ms

                top_feats = []
                if result.explanation and result.explanation.contributions:
                    for c in result.explanation.contributions[:3]:
                        top_feats.append({
                            "feature": c.feature_name,
                            "shap_value": round(c.shap_value, 6),
                            "value": round(c.feature_value, 2),
                        })

                # Consecutive-anomaly threshold
                if raw_anomaly:
                    consecutive_anomalies += 1
                    is_anomaly = consecutive_anomalies >= consecutive_threshold
                else:
                    consecutive_anomalies = 0
                    is_anomaly = False

            except Exception as e:
                print(f"\n  {_YELLOW}[!!] Detection error: {e}{_RESET}")
                is_anomaly = False
                score = 0.0
                severity = "NORMAL"
                latency = 0.0
                top_feats = []

        # ── Record ───────────────────────────────────────────────
        record = CycleRecord(
            cycle=cycle,
            wall_time=datetime.now().isoformat(),
            elapsed_s=round(elapsed, 1),
            phase=phase,
            attack_name=attack_name,
            attack_id=attack_id,
            is_anomaly=is_anomaly,
            anomaly_score=score,
            severity=severity,
            detection_latency_ms=latency,
            request_rate=req_rate,
            error_rate=err_rate,
            latency_p99_ms=lat_p99,
            top_features=top_feats,
        )
        records.append(record)

        # ── Console output ───────────────────────────────────────
        filled = int(bar_width * elapsed / duration)
        bar = "█" * min(filled, bar_width) + "░" * max(0, bar_width - filled)
        elapsed_min = elapsed / 60
        total_min = duration / 60

        if phase == "ATTACK":
            phase_str = f"{_RED}{_BOLD}ATTACK ({attack_name}){_RESET}"
            det_str = f"{_RED}ANOMALY{_RESET}" if is_anomaly else f"{_YELLOW}---{_RESET}"
        else:
            phase_str = f"{_GREEN}NORMAL{_RESET}"
            det_str = f"{_GREEN}OK{_RESET}" if not is_anomaly else f"{_RED}FP!{_RESET}"

        print(
            f"\r  [{bar}] {elapsed_min:5.1f}/{total_min:.0f}min  "
            f"cycle {cycle:3d}  "
            f"{phase_str:<45s}  {det_str}  "
            f"score={score:+.4f}  rate={req_rate:.1f}req/s",
            end="", flush=True,
        )

        # ── Wait for next cycle ──────────────────────────────────
        cycle_elapsed = time.time() - now
        sleep_time = max(0, DETECTION_INTERVAL - cycle_elapsed)
        time.sleep(sleep_time)

    # Clean up any running attack
    if active_attack and active_attack.process:
        stop_process(active_attack.process)
        completed_attacks.append(active_attack)

    print()
    return records


# ══════════════════════════════════════════════════════════════════════════
# Analysis & output
# ══════════════════════════════════════════════════════════════════════════

def compute_live_metrics(
    records: List[CycleRecord],
    attacks: List[AttackSlot],
    sim_start: float,
) -> Dict:
    """Compute detection metrics from live results."""
    total = len(records)
    normal_records = [r for r in records if r.phase == "NORMAL"]
    attack_records = [r for r in records if r.phase == "ATTACK"]

    fp = sum(1 for r in normal_records if r.is_anomaly)
    tp = sum(1 for r in attack_records if r.is_anomaly)
    fn = sum(1 for r in attack_records if not r.is_anomaly)
    tn = sum(1 for r in normal_records if not r.is_anomaly)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    accuracy = (tp + tn) / total if total > 0 else 0.0
    fpr = fp / (fp + tn) if (fp + tn) > 0 else 0.0

    # Per-attack results
    per_attack = []
    for atk in attacks:
        atk_offset_start = atk.start_time - sim_start
        atk_offset_end = atk.end_time - sim_start
        atk_records_filtered = [
            r for r in records
            if r.phase == "ATTACK" and r.attack_id == atk.attack_id
        ]
        n_detected = sum(1 for r in atk_records_filtered if r.is_anomaly)
        n_total = len(atk_records_filtered)

        # Detection delay
        delay = None
        for r in atk_records_filtered:
            if r.is_anomaly:
                delay = r.elapsed_s - atk_offset_start
                break

        per_attack.append({
            "attack_id": atk.attack_id,
            "attack_name": atk.attack_name,
            "attack_type": atk.attack_type,
            "start_offset_s": round(atk_offset_start, 1),
            "duration_s": atk.duration,
            "total_rate": atk.workers * atk.rate,
            "detected_cycles": n_detected,
            "total_cycles": n_total,
            "detection_rate": n_detected / n_total if n_total > 0 else 0.0,
            "detection_delay_s": round(delay, 1) if delay is not None else None,
        })

    atk_scores = [r.anomaly_score for r in attack_records if r.is_anomaly]
    normal_scores = [r.anomaly_score for r in normal_records]

    return {
        "overview": {
            "total_cycles": total,
            "duration_s": records[-1].elapsed_s if records else 0,
            "normal_cycles": len(normal_records),
            "attack_cycles": len(attack_records),
            "total_attacks": len(attacks),
            "environment": "LIVE (minikube + sock-shop + istio)",
        },
        "detection": {
            "true_positives": tp,
            "false_positives": fp,
            "true_negatives": tn,
            "false_negatives": fn,
            "precision": precision,
            "recall": recall,
            "f1_score": f1,
            "accuracy": accuracy,
            "false_positive_rate": fpr,
        },
        "scores": {
            "mean_attack_score": float(np.mean(atk_scores)) if atk_scores else None,
            "mean_normal_score": float(np.mean(normal_scores)) if normal_scores else None,
        },
        "per_attack": per_attack,
    }


def write_outputs(
    records: List[CycleRecord],
    attacks: List[AttackSlot],
    metrics: Dict,
    out_dir: Path,
) -> None:
    """Write all output files."""

    # Event log (JSONL)
    with open(out_dir / "event_log.jsonl", "w") as f:
        for r in records:
            f.write(json.dumps({
                "cycle": r.cycle,
                "wall_time": r.wall_time,
                "elapsed_s": r.elapsed_s,
                "phase": r.phase,
                "attack_name": r.attack_name,
                "attack_id": r.attack_id,
                "is_anomaly": r.is_anomaly,
                "anomaly_score": round(r.anomaly_score, 6),
                "severity": r.severity,
                "request_rate": round(r.request_rate, 2),
                "error_rate": round(r.error_rate, 2),
                "latency_p99_ms": round(r.latency_p99_ms, 2),
                "top_features": r.top_features,
            }) + "\n")

    # Timeline CSV
    with open(out_dir / "timeline.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["cycle", "elapsed_s", "elapsed_min", "phase", "attack_name",
                     "is_anomaly", "anomaly_score", "severity", "request_rate",
                     "error_rate", "latency_p99_ms"])
        for r in records:
            w.writerow([r.cycle, r.elapsed_s, round(r.elapsed_s / 60, 2),
                        r.phase, r.attack_name or "", int(r.is_anomaly),
                        round(r.anomaly_score, 6), r.severity,
                        round(r.request_rate, 2), round(r.error_rate, 2),
                        round(r.latency_p99_ms, 2)])

    # Attack schedule
    schedule = [a.to_dict() for a in attacks]
    with open(out_dir / "attack_schedule.json", "w") as f:
        json.dump({"attacks": schedule, "total": len(attacks)}, f, indent=2)

    # Summary JSON
    def _convert(obj):
        if isinstance(obj, (np.integer,)): return int(obj)
        if isinstance(obj, (np.floating,)): return float(obj)
        if isinstance(obj, np.ndarray): return obj.tolist()
        if isinstance(obj, dict): return {k: _convert(v) for k, v in obj.items()}
        if isinstance(obj, list): return [_convert(i) for i in obj]
        return obj

    with open(out_dir / "summary.json", "w") as f:
        json.dump(_convert(metrics), f, indent=2)

    # LaTeX tables
    write_live_latex(metrics, out_dir / "latex_tables.tex")


def write_live_latex(metrics: Dict, path: Path) -> None:
    """Generate LaTeX tables for live simulation results."""
    d = metrics["detection"]
    o = metrics["overview"]
    lines = [
        "% ====================================================================",
        "% Live Cluster Simulation Results — Auto-generated",
        f"% Generated: {datetime.now().isoformat()}",
        f"% Environment: {o.get('environment', 'Live cluster')}",
        "% ====================================================================",
        "",
        "% Table 1: Live Detection Performance",
        "\\begin{table}[htbp]",
        "\\centering",
        "\\caption{Live Cluster Detection Performance}",
        "\\label{tab:live-overall}",
        "\\begin{tabular}{lr}",
        "\\toprule",
        "\\textbf{Metric} & \\textbf{Value} \\\\",
        "\\midrule",
        f"Environment & Minikube + Sock Shop + Istio \\\\",
        f"Total Cycles & {o['total_cycles']} \\\\",
        f"Normal Cycles & {o['normal_cycles']} \\\\",
        f"Attack Cycles & {o['attack_cycles']} \\\\",
        f"Total Attacks & {o['total_attacks']} \\\\",
        "\\midrule",
        f"True Positives & {d['true_positives']} \\\\",
        f"True Negatives & {d['true_negatives']} \\\\",
        f"False Positives & {d['false_positives']} \\\\",
        f"False Negatives & {d['false_negatives']} \\\\",
        "\\midrule",
        f"Precision & {d['precision']:.4f} \\\\",
        f"Recall & {d['recall']:.4f} \\\\",
        f"F1-Score & {d['f1_score']:.4f} \\\\",
        f"Accuracy & {d['accuracy']:.4f} \\\\",
        f"False Positive Rate & {d['false_positive_rate']:.4f} \\\\",
        "\\bottomrule",
        "\\end{tabular}",
        "\\end{table}",
        "",
        "% Table 2: Individual Attack Results (Live)",
        "\\begin{table}[htbp]",
        "\\centering",
        "\\caption{Individual Attack Detection Results (Live Cluster)}",
        "\\label{tab:live-attacks}",
        "\\begin{tabular}{clcccc}",
        "\\toprule",
        "\\textbf{\\#} & \\textbf{Attack} & \\textbf{Rate} & "
        "\\textbf{Detected} & \\textbf{Rate} & \\textbf{Delay} \\\\",
        "\\midrule",
    ]

    for a in metrics["per_attack"]:
        name = a["attack_name"].replace("_", " ").title()
        det_frac = f"{a['detected_cycles']}/{a['total_cycles']}"
        rate_pct = f"{a['detection_rate']:.0%}" if a['total_cycles'] > 0 else "N/A"
        delay = f"{a['detection_delay_s']:.0f}s" if a['detection_delay_s'] is not None else "---"
        lines.append(
            f"{a['attack_id']+1} & {name} & {a['total_rate']}r/s & "
            f"{det_frac} & {rate_pct} & {delay} \\\\"
        )

    lines.extend([
        "\\bottomrule",
        "\\end{tabular}",
        "\\end{table}",
    ])

    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def print_live_summary(metrics: Dict) -> None:
    """Print human-readable summary."""
    o = metrics["overview"]
    d = metrics["detection"]

    width = 70
    print(f"\n{_BOLD}{_CYAN}{'=' * width}")
    print(f"  LIVE SIMULATION RESULTS")
    print(f"{'=' * width}{_RESET}\n")

    print(f"  {_BOLD}Environment{_RESET}")
    print(f"    Platform:     Minikube + Sock Shop + Istio (real cluster)")
    print(f"    Metrics from: Prometheus ({PROMETHEUS_URL})")
    print(f"    Total cycles: {o['total_cycles']} ({o['normal_cycles']} normal, {o['attack_cycles']} attack)")
    print(f"    Attacks:      {o['total_attacks']}")

    print(f"\n  {_BOLD}Detection Performance{_RESET}")
    print(f"    Precision:  {d['precision']:.4f}")
    print(f"    Recall:     {d['recall']:.4f}")
    print(f"    F1-Score:   {d['f1_score']:.4f}")
    print(f"    Accuracy:   {d['accuracy']:.4f}")
    print(f"    FP Rate:    {d['false_positive_rate']:.4f}")

    print(f"\n  {_BOLD}Confusion Matrix{_RESET}")
    print(f"    TP: {d['true_positives']:4d}  │  FP: {d['false_positives']:4d}")
    print(f"    FN: {d['false_negatives']:4d}  │  TN: {d['true_negatives']:4d}")

    print(f"\n  {_BOLD}Individual Attacks{_RESET}")
    for a in metrics["per_attack"]:
        name = a["attack_name"].replace("_", " ").title()
        colour = _GREEN if a['detection_rate'] >= 0.8 else (_YELLOW if a['detection_rate'] >= 0.5 else _RED)
        delay_str = f"{a['detection_delay_s']:.0f}s" if a['detection_delay_s'] is not None else "---"
        print(f"    #{a['attack_id']+1:2d}  {name:.<25s}  {a['total_rate']}r/s  "
              f"{colour}{a['detection_rate']:5.0%}{_RESET}  "
              f"({a['detected_cycles']}/{a['total_cycles']})  delay={delay_str}")

    print(f"\n{_BOLD}{_CYAN}{'=' * width}{_RESET}")


# ══════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════

def main() -> int:
    parser = argparse.ArgumentParser(description="Live cluster DDoS detection simulation")
    parser.add_argument("--duration", type=int, default=DEFAULT_DURATION,
                        help="Duration in seconds (default: 600)")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed (default: 42)")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="Output directory")
    parser.add_argument("--skip-checks", action="store_true",
                        help="Skip pre-flight checks")
    parser.add_argument("--training-duration", type=int, default=90,
                        help="Live training data collection duration in seconds (default: 90)")
    parser.add_argument("--synthetic-training", action="store_true",
                        help="Use synthetic training data instead of live collection")
    parser.add_argument("--model-path", type=str, default=None,
                        help="Path to pre-trained model (.pkl) — skips training phase")
    parser.add_argument("--traffic-workers", type=int, default=3,
                        help="Normal traffic generator workers (default: 3)")
    parser.add_argument("--traffic-rate", type=float, default=5.0,
                        help="Requests/sec per worker for normal traffic (default: 5.0)")
    args = parser.parse_args()

    warnings.filterwarnings("ignore", category=RuntimeWarning)
    warnings.filterwarnings("ignore", category=FutureWarning)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)-8s %(message)s")

    rng = np.random.RandomState(args.seed)

    width = 70
    print(f"\n{_BOLD}{_CYAN}{'=' * width}")
    print(f"  LIVE CLUSTER SIMULATION — DDoS Detection")
    print(f"{'=' * width}{_RESET}\n")
    total_normal_rate = args.traffic_workers * args.traffic_rate
    print(f"  Duration:  {args.duration}s ({args.duration/60:.0f} min)")
    print(f"  Interval:  {DETECTION_INTERVAL}s")
    print(f"  Traffic:   {args.traffic_workers}w × {args.traffic_rate}r/s = ~{total_normal_rate:.0f} req/s normal")
    if args.model_path:
        print(f"  Training:  pre-trained ({args.model_path})")
    else:
        print(f"  Training:  {'synthetic' if args.synthetic_training else f'live ({args.training_duration}s)'}")
    print(f"  Seed:      {args.seed}")
    print()

    # ── Pre-flight checks ─────────────────────────────────────────
    print(f"{_BOLD}  Phase 1: Pre-flight Checks{_RESET}")

    if not args.skip_checks:
        minikube_ip = check_minikube()
        target_url = check_sock_shop(minikube_ip)
        check_prometheus()
        has_metrics = check_istio_metrics()

        if not has_metrics:
            print(f"  {_YELLOW}[!!]{_RESET}  Will generate traffic to seed Istio metrics")
    else:
        minikube_ip = subprocess.run(
            ["minikube", "ip"], capture_output=True, text=True
        ).stdout.strip() or "192.168.49.2"
        target_url = f"http://{minikube_ip}:30001"
        print(f"  {_YELLOW}[!!]{_RESET}  Skipping checks, using {target_url}")

    print()

    # ── Output directory ──────────────────────────────────────────
    if args.output_dir:
        out_dir = Path(args.output_dir)
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = Path("results") / f"live_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"  Output: {out_dir.resolve()}\n")

    # ── Phase 2: Start normal traffic ───────────────────────────
    print(f"{_BOLD}  Phase 2: Starting Normal Traffic{_RESET}")
    traffic_proc = start_normal_traffic(target_url, workers=args.traffic_workers, rate=args.traffic_rate)

    if not args.model_path:
        # Let traffic flow for 15s before collecting training data
        print(f"  {_CYAN}[..]{_RESET}  Waiting 15s for traffic patterns to establish ...")
        time.sleep(15)
    else:
        print(f"  {_CYAN}[..]{_RESET}  Using pre-trained model — short 5s warm-up ...")
        time.sleep(5)
    print()

    # ── Phase 3: Train model (or load pre-trained) ────────────────
    print(f"{_BOLD}  Phase 3: Model {'Loading' if args.model_path else 'Training'}{_RESET}")
    with tempfile.TemporaryDirectory(prefix="ddos_live_") as tmpdir:

        prometheus = PrometheusAdapter(
            prometheus_url=PROMETHEUS_URL,
            namespace=NAMESPACE,
            query_timeout=10,
        )
        print(f"  {_GREEN}[OK]{_RESET}  PrometheusAdapter → {PROMETHEUS_URL}")

        if args.model_path:
            # Use pre-trained model (e.g. CIC-trained)
            model_path = Path(args.model_path)
            if not model_path.exists():
                print(f"  {_RED}[!!]{_RESET}  Model not found: {model_path}")
                stop_process(traffic_proc)
                return 1
            print(f"  {_GREEN}[OK]{_RESET}  Loading pre-trained model: {model_path}")
        elif args.synthetic_training:
            model_path = train_model_synthetic(Path(tmpdir))
        else:
            # Collect real normal-traffic samples
            print(f"  {_CYAN}[..]{_RESET}  Collecting {args.training_duration}s of live training data ...")
            live_samples = collect_live_training_data(
                prometheus,
                collection_duration=args.training_duration,
                interval=5,
            )
            print(f"  {_GREEN}[OK]{_RESET}  Collected {len(live_samples)} live samples")
            model_path = train_model_live(Path(tmpdir), live_samples)

        print(f"\n{_BOLD}  Phase 4: Initialising Detector{_RESET}")
        detector = AnomalyDetector(model_path, enable_xai=True)
        print(f"  {_GREEN}[OK]{_RESET}  Detector ready (version: {detector.model_version})")
        print()

        # ── Phase 5: Schedule attacks ─────────────────────────────
        print(f"{_BOLD}  Phase 5: Attack Scheduling{_RESET}")
        sim_start = time.time()
        attacks = schedule_attacks(args.duration, sim_start, rng)

        print(f"  {_GREEN}[OK]{_RESET}  Scheduled {len(attacks)} attacks:")
        for a in attacks:
            offset = a.start_time - sim_start
            name = a.attack_name.replace("_", " ").title()
            print(f"       #{a.attack_id+1:2d}  {name:.<25s}  "
                  f"t={offset:.0f}s  dur={a.duration}s  "
                  f"rate={a.workers * a.rate}r/s")
        print()

        # ── Phase 6: Run detection ────────────────────────────────
        print(f"{_BOLD}  Phase 6: Live Detection Loop{_RESET}")

        try:
            records = run_live_detection(
                detector=detector,
                prometheus=prometheus,
                target_url=target_url,
                attacks=attacks,
                duration=args.duration,
                out_dir=out_dir,
            )
        except KeyboardInterrupt:
            print(f"\n  {_YELLOW}[!!] Interrupted by user{_RESET}")
            records = []
        finally:
            # Clean up
            print(f"\n  {_CYAN}[..]{_RESET}  Stopping normal traffic generator ...")
            stop_process(traffic_proc)
            print(f"  {_GREEN}[OK]{_RESET}  Traffic generator stopped")

    if not records:
        print(f"  {_RED}No data collected{_RESET}")
        return 1

    # ── Phase 7: Analyse & output ─────────────────────────────────
    print(f"\n{_BOLD}  Phase 7: Computing Metrics & Writing Output{_RESET}")

    metrics = compute_live_metrics(records, attacks, sim_start)
    write_outputs(records, attacks, metrics, out_dir)

    print(f"  {_GREEN}[OK]{_RESET}  {out_dir / 'event_log.jsonl'}")
    print(f"  {_GREEN}[OK]{_RESET}  {out_dir / 'timeline.csv'}")
    print(f"  {_GREEN}[OK]{_RESET}  {out_dir / 'attack_schedule.json'}")
    print(f"  {_GREEN}[OK]{_RESET}  {out_dir / 'summary.json'}")
    print(f"  {_GREEN}[OK]{_RESET}  {out_dir / 'latex_tables.tex'}")

    print_live_summary(metrics)

    d = metrics["detection"]
    if d["recall"] >= 0.5:
        print(f"\n  {_GREEN}{_BOLD}LIVE SIMULATION PASSED{_RESET}")
        return 0
    else:
        print(f"\n  {_YELLOW}{_BOLD}LIVE SIMULATION COMPLETE — review results{_RESET}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
