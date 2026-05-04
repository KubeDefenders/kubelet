#!/usr/bin/env python3
"""
Extended simulation: 1-hour monitoring session with randomised attacks.

Simulates a realistic monitoring session where:
  - Normal traffic persists throughout (with occasional idle drops to 0)
  - 8-15 non-overlapping attacks are randomly scheduled
  - 6 different attack types are used with distinct traffic signatures
  - All results are recorded in research-publishable format

This is a *simulation* — it does NOT require a live Kubernetes cluster.
It exercises the full detection_v2 pipeline (feature extraction, model
inference, SHAP explanation, consecutive-anomaly alerting) against
synthetic MetricSamples that model realistic traffic patterns.

Output (under results/simulation_<timestamp>/):
    event_log.jsonl          Every detection cycle
    attack_schedule.json     Pre-planned schedule with metadata
    summary.json             Overall statistics & per-type metrics
    timeline.csv             For plotting (cycle, time, phase, score, ...)
    latex_tables.tex         Publication-ready tables for the paper

Usage:
    python -m detection_v2.extended_simulation
    python -m detection_v2.extended_simulation --seed 42 --duration 3600
    python -m detection_v2.extended_simulation --real-time   # 15s between cycles
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import tempfile
import time
import warnings
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from detection_v2.core.schema import MetricSample, DetectionResult, Severity
from detection_v2.core.model import AnomalyDetector
from detection_v2.training.trainer import Trainer
from detection_v2.monitor.continuous import ContinuousMonitor

# ──────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────

INTERVAL_SECONDS = 15        # Detection cycle interval
DEFAULT_DURATION = 3600      # 1 hour
DEFAULT_SEED = 42

# Attack type identifiers
ATTACK_TYPES = [
    "volumetric_flood",
    "crossfire_link_flood",
    "slowloris",
    "syn_flood",
    "amplification",
    "http_flood",
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
class ScheduledAttack:
    """A single attack in the pre-planned schedule."""
    attack_id: int
    attack_type: str
    start_cycle: int
    end_cycle: int          # exclusive
    start_time_s: float     # simulated seconds from epoch start
    end_time_s: float
    duration_s: float

    @property
    def n_cycles(self) -> int:
        return self.end_cycle - self.start_cycle


@dataclass
class CycleRecord:
    """Record for a single detection cycle."""
    cycle: int
    simulated_time_s: float
    simulated_timestamp: str   # ISO-8601
    phase: str                 # NORMAL | IDLE | ATTACK
    attack_type: Optional[str]
    attack_id: Optional[int]
    is_anomaly: bool
    anomaly_score: float
    severity: str
    detection_latency_ms: float
    top_features: List[Dict]   # [{feature, shap_value, value}, ...]


# ══════════════════════════════════════════════════════════════════════════
# Traffic generators — one per attack type + normal + idle
# ══════════════════════════════════════════════════════════════════════════

def _make_normal_sample(rng: np.random.RandomState, ts: float,
                        hour_progress: float = 0.5) -> MetricSample:
    """
    Generate one normal-traffic MetricSample.

    hour_progress (0..1) adds slight diurnal variation: traffic is
    slightly higher in the middle of the hour (warm-up / cool-down).
    """
    diurnal = 0.8 + 0.4 * np.sin(np.pi * hour_progress)  # 0.8 – 1.2 multiplier
    base_rate = 50.0 * diurnal

    return MetricSample(
        timestamp=ts,
        request_rate=max(0.0, base_rate + rng.normal(0, 5)),
        request_rate_variance=max(0.0, 4.0 + rng.normal(0, 1)),
        latency_p50_ms=max(0.0, 12.0 + rng.normal(0, 2)),
        latency_p95_ms=max(0.0, 45.0 + rng.normal(0, 5)),
        latency_p99_ms=max(0.0, 120.0 + rng.normal(0, 10)),
        error_rate=max(0.0, 0.5 + rng.normal(0, 0.2)),
        total_request_rate=max(0.001, base_rate + rng.normal(0, 5)),
        byte_rate_in=max(0.0, 25000.0 * diurnal + rng.normal(0, 2000)),
        byte_rate_out=max(0.0, 125000.0 * diurnal + rng.normal(0, 10000)),
        avg_request_size_bytes=max(0.0, 500.0 + rng.normal(0, 50)),
        avg_response_size_bytes=max(0.0, 2500.0 + rng.normal(0, 200)),
        connection_open_rate=max(0.0, 8.0 + rng.normal(0, 1)),
        connection_close_rate=max(0.0, 7.5 + rng.normal(0, 1)),
    )


def _make_idle_sample(ts: float) -> MetricSample:
    """Zero-traffic sample."""
    return MetricSample.zero()._replace(timestamp=ts) if hasattr(MetricSample, '_replace') else MetricSample(
        timestamp=ts,
        request_rate=0.0, request_rate_variance=0.0,
        latency_p50_ms=0.0, latency_p95_ms=0.0, latency_p99_ms=0.0,
        error_rate=0.0, total_request_rate=0.0,
        byte_rate_in=0.0, byte_rate_out=0.0,
        avg_request_size_bytes=0.0, avg_response_size_bytes=0.0,
        connection_open_rate=0.0, connection_close_rate=0.0,
    )


# ── Attack generators ────────────────────────────────────────────────────

def _make_volumetric_flood_sample(rng: np.random.RandomState, ts: float,
                                  intensity: float = 1.0) -> MetricSample:
    """
    Volumetric DDoS: massive request rate, high variance, high errors.
    """
    rate = 5000.0 * intensity
    return MetricSample(
        timestamp=ts,
        request_rate=max(0.0, rate + rng.normal(0, 500 * intensity)),
        request_rate_variance=max(0.0, 2000.0 * intensity + rng.normal(0, 300)),
        latency_p50_ms=max(0.0, 500.0 + rng.normal(0, 100)),
        latency_p95_ms=max(0.0, 3000.0 + rng.normal(0, 500)),
        latency_p99_ms=max(0.0, 8000.0 + rng.normal(0, 1000)),
        error_rate=max(0.0, 400.0 * intensity + rng.normal(0, 50)),
        total_request_rate=max(0.001, rate + rng.normal(0, 500 * intensity)),
        byte_rate_in=max(0.0, 5_000_000.0 * intensity + rng.normal(0, 500_000)),
        byte_rate_out=max(0.0, 500_000.0 + rng.normal(0, 50_000)),
        avg_request_size_bytes=max(0.0, 100.0 + rng.normal(0, 20)),
        avg_response_size_bytes=max(0.0, 50.0 + rng.normal(0, 10)),
        connection_open_rate=max(0.0, 2000.0 * intensity + rng.normal(0, 200)),
        connection_close_rate=max(0.0, 200.0 + rng.normal(0, 50)),
    )


def _make_crossfire_sample(rng: np.random.RandomState, ts: float,
                           intensity: float = 1.0) -> MetricSample:
    """
    Crossfire / link-flood: moderate request rate but extreme
    byte-rate asymmetry, connection churn, and tail latency.
    """
    return MetricSample(
        timestamp=ts,
        request_rate=max(0.0, 80.0 + rng.normal(0, 10)),
        request_rate_variance=max(0.0, 45.0 * intensity + rng.normal(0, 8)),
        latency_p50_ms=max(0.0, 200.0 * intensity + rng.normal(0, 30)),
        latency_p95_ms=max(0.0, 1500.0 * intensity + rng.normal(0, 200)),
        latency_p99_ms=max(0.0, 5000.0 * intensity + rng.normal(0, 500)),
        error_rate=max(0.0, 15.0 * intensity + rng.normal(0, 3)),
        total_request_rate=max(0.001, 80.0 + rng.normal(0, 10)),
        byte_rate_in=max(0.0, 3_000_000.0 * intensity + rng.normal(0, 300_000)),
        byte_rate_out=max(0.0, 60_000.0 + rng.normal(0, 8000)),
        avg_request_size_bytes=max(0.0, 1200.0 + rng.normal(0, 100)),
        avg_response_size_bytes=max(0.0, 100.0 + rng.normal(0, 20)),
        connection_open_rate=max(0.0, 500.0 * intensity + rng.normal(0, 50)),
        connection_close_rate=max(0.0, 50.0 + rng.normal(0, 10)),
    )


def _make_slowloris_sample(rng: np.random.RandomState, ts: float,
                           intensity: float = 1.0) -> MetricSample:
    """
    Slowloris: low request rate, extremely high latency, many open
    connections that never close.
    """
    return MetricSample(
        timestamp=ts,
        request_rate=max(0.0, 10.0 + rng.normal(0, 2)),
        request_rate_variance=max(0.0, 2.0 + rng.normal(0, 0.5)),
        latency_p50_ms=max(0.0, 2000.0 * intensity + rng.normal(0, 300)),
        latency_p95_ms=max(0.0, 15000.0 * intensity + rng.normal(0, 2000)),
        latency_p99_ms=max(0.0, 29000.0 * intensity + rng.normal(0, 3000)),
        error_rate=max(0.0, 3.0 + rng.normal(0, 1)),
        total_request_rate=max(0.001, 10.0 + rng.normal(0, 2)),
        byte_rate_in=max(0.0, 1000.0 + rng.normal(0, 200)),
        byte_rate_out=max(0.0, 500.0 + rng.normal(0, 100)),
        avg_request_size_bytes=max(0.0, 50.0 + rng.normal(0, 10)),
        avg_response_size_bytes=max(0.0, 50.0 + rng.normal(0, 10)),
        connection_open_rate=max(0.0, 800.0 * intensity + rng.normal(0, 100)),
        connection_close_rate=max(0.0, 5.0 + rng.normal(0, 2)),
    )


def _make_syn_flood_sample(rng: np.random.RandomState, ts: float,
                           intensity: float = 1.0) -> MetricSample:
    """
    SYN flood: extremely high connection open rate, near-zero close rate,
    moderate request rate (half-open connections don't complete).
    """
    return MetricSample(
        timestamp=ts,
        request_rate=max(0.0, 100.0 + rng.normal(0, 20)),
        request_rate_variance=max(0.0, 80.0 * intensity + rng.normal(0, 15)),
        latency_p50_ms=max(0.0, 300.0 + rng.normal(0, 50)),
        latency_p95_ms=max(0.0, 2000.0 + rng.normal(0, 300)),
        latency_p99_ms=max(0.0, 5000.0 + rng.normal(0, 800)),
        error_rate=max(0.0, 50.0 * intensity + rng.normal(0, 10)),
        total_request_rate=max(0.001, 100.0 + rng.normal(0, 20)),
        byte_rate_in=max(0.0, 200_000.0 * intensity + rng.normal(0, 30_000)),
        byte_rate_out=max(0.0, 20_000.0 + rng.normal(0, 3000)),
        avg_request_size_bytes=max(0.0, 64.0 + rng.normal(0, 8)),
        avg_response_size_bytes=max(0.0, 0.0 + rng.uniform(0, 5)),
        connection_open_rate=max(0.0, 5000.0 * intensity + rng.normal(0, 500)),
        connection_close_rate=max(0.0, 10.0 + rng.normal(0, 3)),
    )


def _make_amplification_sample(rng: np.random.RandomState, ts: float,
                               intensity: float = 1.0) -> MetricSample:
    """
    Amplification attack: small requests trigger huge responses.
    Moderate request rate, low inbound bytes, extremely high outbound bytes.
    """
    return MetricSample(
        timestamp=ts,
        request_rate=max(0.0, 200.0 * intensity + rng.normal(0, 30)),
        request_rate_variance=max(0.0, 50.0 * intensity + rng.normal(0, 10)),
        latency_p50_ms=max(0.0, 80.0 + rng.normal(0, 15)),
        latency_p95_ms=max(0.0, 400.0 + rng.normal(0, 60)),
        latency_p99_ms=max(0.0, 1500.0 + rng.normal(0, 200)),
        error_rate=max(0.0, 20.0 * intensity + rng.normal(0, 5)),
        total_request_rate=max(0.001, 200.0 * intensity + rng.normal(0, 30)),
        byte_rate_in=max(0.0, 10_000.0 + rng.normal(0, 1500)),
        byte_rate_out=max(0.0, 8_000_000.0 * intensity + rng.normal(0, 800_000)),
        avg_request_size_bytes=max(0.0, 50.0 + rng.normal(0, 10)),
        avg_response_size_bytes=max(0.0, 40_000.0 * intensity + rng.normal(0, 5000)),
        connection_open_rate=max(0.0, 150.0 * intensity + rng.normal(0, 30)),
        connection_close_rate=max(0.0, 140.0 + rng.normal(0, 25)),
    )


def _make_http_flood_sample(rng: np.random.RandomState, ts: float,
                            intensity: float = 1.0) -> MetricSample:
    """
    HTTP flood: high request rate with plausible-looking sizes.
    Harder to detect — looks like legitimate traffic but at huge volume.
    """
    rate = 2000.0 * intensity
    return MetricSample(
        timestamp=ts,
        request_rate=max(0.0, rate + rng.normal(0, 200 * intensity)),
        request_rate_variance=max(0.0, 800.0 * intensity + rng.normal(0, 100)),
        latency_p50_ms=max(0.0, 50.0 + rng.normal(0, 10)),
        latency_p95_ms=max(0.0, 200.0 + rng.normal(0, 30)),
        latency_p99_ms=max(0.0, 800.0 + rng.normal(0, 100)),
        error_rate=max(0.0, 100.0 * intensity + rng.normal(0, 20)),
        total_request_rate=max(0.001, rate + rng.normal(0, 200 * intensity)),
        byte_rate_in=max(0.0, 1_000_000.0 * intensity + rng.normal(0, 100_000)),
        byte_rate_out=max(0.0, 5_000_000.0 * intensity + rng.normal(0, 500_000)),
        avg_request_size_bytes=max(0.0, 500.0 + rng.normal(0, 50)),
        avg_response_size_bytes=max(0.0, 2500.0 + rng.normal(0, 250)),
        connection_open_rate=max(0.0, 500.0 * intensity + rng.normal(0, 60)),
        connection_close_rate=max(0.0, 480.0 * intensity + rng.normal(0, 55)),
    )


_ATTACK_GENERATORS = {
    "volumetric_flood": _make_volumetric_flood_sample,
    "crossfire_link_flood": _make_crossfire_sample,
    "slowloris": _make_slowloris_sample,
    "syn_flood": _make_syn_flood_sample,
    "amplification": _make_amplification_sample,
    "http_flood": _make_http_flood_sample,
}


def generate_sample(
    rng: np.random.RandomState,
    ts: float,
    phase: str,
    attack_type: Optional[str] = None,
    hour_progress: float = 0.5,
    attack_progress: float = 0.5,
) -> MetricSample:
    """
    Generate a MetricSample for the given phase.

    attack_progress (0..1) controls intensity ramp-up/down for attacks.
    """
    if phase == "IDLE":
        return _make_idle_sample(ts)
    elif phase == "NORMAL":
        return _make_normal_sample(rng, ts, hour_progress)
    elif phase == "ATTACK" and attack_type:
        # Ramp intensity: start at 0.6, peak at 1.0-1.2 mid-attack, taper
        intensity = 0.6 + 0.6 * np.sin(np.pi * attack_progress)
        gen = _ATTACK_GENERATORS[attack_type]
        return gen(rng, ts, intensity)
    else:
        return _make_normal_sample(rng, ts, hour_progress)


# ══════════════════════════════════════════════════════════════════════════
# Attack scheduler
# ══════════════════════════════════════════════════════════════════════════

def schedule_attacks(
    total_cycles: int,
    rng: np.random.RandomState,
    interval_s: int = INTERVAL_SECONDS,
) -> List[ScheduledAttack]:
    """
    Create a random, non-overlapping attack schedule.

    Rules:
      - Warm-up: first 8-20 cycles are always normal (2-5 min)
      - Cool-down: last 4 cycles are always normal (1 min)
      - Attacks last 4-20 cycles (1-5 min)
      - Gaps between attacks: 8-32 cycles (2-8 min)
      - Number of attacks: 8-15
      - Attack types drawn from ATTACK_TYPES
    """
    warmup_cycles = rng.randint(8, 21)
    cooldown_cycles = 4
    available = total_cycles - warmup_cycles - cooldown_cycles

    attacks: List[ScheduledAttack] = []
    cursor = warmup_cycles
    attack_id = 0

    # Try to fit as many attacks as possible (target 8-15)
    target_count = rng.randint(8, 16)

    for _ in range(target_count):
        # Duration: 4-20 cycles (1-5 minutes)
        duration = rng.randint(4, 21)
        # Gap after this attack: 8-32 cycles (2-8 minutes)
        gap = rng.randint(8, 33)

        # Check if we have room
        if cursor + duration > total_cycles - cooldown_cycles:
            break

        attack_type = rng.choice(ATTACK_TYPES)
        attack = ScheduledAttack(
            attack_id=attack_id,
            attack_type=attack_type,
            start_cycle=cursor,
            end_cycle=cursor + duration,
            start_time_s=cursor * interval_s,
            end_time_s=(cursor + duration) * interval_s,
            duration_s=duration * interval_s,
        )
        attacks.append(attack)
        attack_id += 1
        cursor += duration + gap

    return attacks


def plan_idle_periods(
    total_cycles: int,
    attacks: List[ScheduledAttack],
    rng: np.random.RandomState,
) -> List[Tuple[int, int]]:
    """
    Scatter 3-8 idle periods (2-8 cycles each) in non-attack windows.

    Returns list of (start_cycle, end_cycle) pairs.
    """
    # Build a set of attack cycles
    attack_cycles = set()
    for a in attacks:
        attack_cycles.update(range(a.start_cycle, a.end_cycle))

    # Find contiguous normal windows
    normal_windows: List[Tuple[int, int]] = []
    start = None
    for c in range(total_cycles):
        if c not in attack_cycles:
            if start is None:
                start = c
        else:
            if start is not None:
                normal_windows.append((start, c))
                start = None
    if start is not None:
        normal_windows.append((start, total_cycles))

    # Place idle periods in large-enough normal windows
    n_idle = rng.randint(3, 9)
    idle_periods: List[Tuple[int, int]] = []

    # Filter windows large enough to host an idle period (min 4 cycles)
    eligible = [(s, e) for s, e in normal_windows if e - s >= 6]

    for _ in range(n_idle):
        if not eligible:
            break
        idx = rng.randint(0, len(eligible))
        ws, we = eligible[idx]
        idle_len = rng.randint(2, min(9, we - ws - 1))
        # Place idle period somewhere inside the window (not at edges)
        margin = max(1, (we - ws - idle_len) // 2)
        offset = rng.randint(1, max(2, we - ws - idle_len))
        idle_start = ws + offset
        idle_end = min(idle_start + idle_len, we - 1)

        if idle_end > idle_start:
            idle_periods.append((idle_start, idle_end))

        # Remove used window to spread idle periods out
        eligible.pop(idx)

    return idle_periods


# ══════════════════════════════════════════════════════════════════════════
# Training
# ══════════════════════════════════════════════════════════════════════════

def train_model(model_dir: Path, seed: int) -> Path:
    """Train Isolation Forest on synthetic normal traffic."""
    rng = np.random.RandomState(seed)

    print(f"  {_CYAN}[..]{_RESET}  Generating 800 diverse normal + 100 attack training samples ...")
    # Generate normal samples with diverse diurnal patterns to prevent
    # false positives when traffic naturally varies over the hour
    normal_samples = []
    for i in range(800):
        hour_prog = i / 800.0  # Full 0→1 sweep of diurnal variation
        normal_samples.append(_make_normal_sample(rng, time.time() + i, hour_prog))

    attack_rng = np.random.RandomState(seed + 1)
    attack_samples = []
    for i in range(100):
        atype = ATTACK_TYPES[i % len(ATTACK_TYPES)]
        gen = _ATTACK_GENERATORS[atype]
        attack_samples.append(gen(attack_rng, time.time() + i))

    print(f"  {_CYAN}[..]{_RESET}  Training Isolation Forest (200 estimators, contamination=0.05) ...")
    trainer = Trainer(
        n_estimators=200,
        max_samples=256,
        shap_background_size=100,
        contamination=0.05,
    )
    results = trainer.fit(normal_samples, attack_samples=attack_samples)

    model_path = model_dir / "simulation_model.pkl"
    trainer.save(model_path)

    print(f"  {_GREEN}[OK]{_RESET}  Model saved → {model_path}")
    print(f"  {_GREEN}[OK]{_RESET}  Training accuracy: {results['accuracy']:.1%}")
    print(f"  {_GREEN}[OK]{_RESET}  Attack recall:     {results['attack_recall']:.1%}")
    print(f"  {_GREEN}[OK]{_RESET}  Normal FP rate:    {results.get('normal_false_positive_rate', 0):.1%}")
    return model_path


# ══════════════════════════════════════════════════════════════════════════
# Core simulation loop
# ══════════════════════════════════════════════════════════════════════════

def run_simulation(
    detector: AnomalyDetector,
    total_cycles: int,
    attacks: List[ScheduledAttack],
    idle_periods: List[Tuple[int, int]],
    seed: int,
    real_time: bool = False,
    *,
    consecutive_threshold: int = 2,
    min_request_rate: float = 1.0,
) -> List[CycleRecord]:
    """
    Run the full simulation cycle-by-cycle.

    Mirrors ContinuousMonitor's production behaviour:
      - Idle-traffic guard: skip detection when request_rate < min_request_rate
      - Consecutive-anomaly threshold: only flag "confirmed anomaly" after
        consecutive_threshold consecutive raw anomalies

    Returns a list of CycleRecord for every cycle.
    """
    rng = np.random.RandomState(seed + 100)

    # Build lookup structures
    attack_map: Dict[int, ScheduledAttack] = {}
    for a in attacks:
        for c in range(a.start_cycle, a.end_cycle):
            attack_map[c] = a

    idle_set = set()
    for s, e in idle_periods:
        idle_set.update(range(s, e))

    base_time = time.time()
    records: List[CycleRecord] = []

    # Consecutive-anomaly tracking (mirrors ContinuousMonitor)
    _consecutive_anomalies = 0
    _consecutive_normal = 0

    # Progress bar width
    bar_width = 50

    for cycle in range(total_cycles):
        sim_time = base_time + cycle * INTERVAL_SECONDS
        hour_progress = cycle / total_cycles
        sim_ts_iso = datetime.fromtimestamp(sim_time).isoformat()

        # Determine phase
        if cycle in attack_map:
            attack = attack_map[cycle]
            phase = "ATTACK"
            attack_type = attack.attack_type
            attack_id = attack.attack_id
            # Progress within attack
            attack_progress = (cycle - attack.start_cycle) / max(1, attack.n_cycles - 1)
        elif cycle in idle_set:
            phase = "IDLE"
            attack_type = None
            attack_id = None
            attack_progress = 0.0
        else:
            phase = "NORMAL"
            attack_type = None
            attack_id = None
            attack_progress = 0.0

        # Generate sample
        sample = generate_sample(
            rng, sim_time, phase, attack_type, hour_progress, attack_progress
        )

        # ── Idle-traffic guard (same as ContinuousMonitor) ────────
        # When traffic is zero/near-zero, the model is out-of-distribution.
        # We cannot detect a DDoS on an idle system.
        if sample.request_rate < min_request_rate:
            is_anomaly = False
            score = 0.0
            severity = "NORMAL"
            latency = 0.0
            top_feats = []
            _consecutive_anomalies = 0
            _consecutive_normal += 1
        else:
            # Run detection
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

                # ── Consecutive-anomaly threshold ─────────────────
                # Only confirm anomaly after consecutive_threshold
                # consecutive raw anomalies (reduces single-cycle FPs)
                if raw_anomaly:
                    _consecutive_anomalies += 1
                    _consecutive_normal = 0
                    is_anomaly = _consecutive_anomalies >= consecutive_threshold
                else:
                    _consecutive_anomalies = 0
                    _consecutive_normal += 1
                    is_anomaly = False

            except Exception:
                is_anomaly = False
                score = 0.0
                severity = "NORMAL"
                latency = 0.0
                top_feats = []

        record = CycleRecord(
            cycle=cycle,
            simulated_time_s=cycle * INTERVAL_SECONDS,
            simulated_timestamp=sim_ts_iso,
            phase=phase,
            attack_type=attack_type,
            attack_id=attack_id,
            is_anomaly=is_anomaly,
            anomaly_score=score,
            severity=severity,
            detection_latency_ms=latency,
            top_features=top_feats,
        )
        records.append(record)

        # ── Live console output ──────────────────────────────────────
        # Progress bar
        filled = int(bar_width * (cycle + 1) / total_cycles)
        bar = "█" * filled + "░" * (bar_width - filled)
        elapsed_sim = (cycle + 1) * INTERVAL_SECONDS
        elapsed_min = elapsed_sim / 60
        total_min = total_cycles * INTERVAL_SECONDS / 60

        # Status indicator
        if phase == "ATTACK":
            phase_str = f"{_RED}{_BOLD}ATTACK ({attack_type}){_RESET}"
            det_str = f"{_RED}ANOMALY{_RESET}" if is_anomaly else f"{_YELLOW}MISSED{_RESET}"
        elif phase == "IDLE":
            phase_str = f"{_DIM}IDLE{_RESET}"
            det_str = f"{_GREEN}OK{_RESET}" if not is_anomaly else f"{_RED}FP!{_RESET}"
        else:
            phase_str = f"{_GREEN}NORMAL{_RESET}"
            det_str = f"{_GREEN}OK{_RESET}" if not is_anomaly else f"{_RED}FP!{_RESET}"

        # Print compact line
        print(
            f"\r  [{bar}] {elapsed_min:5.1f}/{total_min:.0f}min  "
            f"cycle {cycle+1:3d}/{total_cycles}  "
            f"{phase_str:<45s}  {det_str}  score={score:+.4f}",
            end="", flush=True,
        )

        # Print newline on attack transitions or alerts
        if phase == "ATTACK" and (cycle == 0 or attack_map.get(cycle - 1) is None):
            print(f"\n  {_RED}>>> Attack #{attack_id} started: {attack_type} "
                  f"(cycles {attack_map[cycle].start_cycle}-{attack_map[cycle].end_cycle - 1}){_RESET}")
        elif phase != "ATTACK" and cycle > 0 and (cycle - 1) in attack_map:
            print(f"\n  {_GREEN}<<< Attack ended, returning to {phase}{_RESET}")

        if real_time:
            time.sleep(INTERVAL_SECONDS)

    print()  # Final newline after progress bar
    return records


# ══════════════════════════════════════════════════════════════════════════
# Analysis & output generation
# ══════════════════════════════════════════════════════════════════════════

def compute_metrics(
    records: List[CycleRecord],
    attacks: List[ScheduledAttack],
) -> Dict:
    """Compute comprehensive detection metrics."""

    # Overall counts
    total = len(records)
    normal_records = [r for r in records if r.phase == "NORMAL"]
    idle_records = [r for r in records if r.phase == "IDLE"]
    attack_records = [r for r in records if r.phase == "ATTACK"]

    # False positives (anomaly detected during NORMAL or IDLE)
    fp_normal = sum(1 for r in normal_records if r.is_anomaly)
    fp_idle = sum(1 for r in idle_records if r.is_anomaly)
    total_fp = fp_normal + fp_idle

    # True positives (anomaly detected during ATTACK)
    tp = sum(1 for r in attack_records if r.is_anomaly)
    # False negatives (no anomaly during ATTACK)
    fn = sum(1 for r in attack_records if not r.is_anomaly)
    # True negatives (no anomaly during NORMAL/IDLE)
    tn = len(normal_records) + len(idle_records) - total_fp

    precision = tp / (tp + total_fp) if (tp + total_fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    accuracy = (tp + tn) / total if total > 0 else 0.0
    fpr = total_fp / (total_fp + tn) if (total_fp + tn) > 0 else 0.0

    # Per-attack analysis
    per_attack = []
    for attack in attacks:
        atk_records = [r for r in records
                       if r.cycle >= attack.start_cycle and r.cycle < attack.end_cycle]
        detected_cycles = [r for r in atk_records if r.is_anomaly]
        n_detected = len(detected_cycles)
        n_total = len(atk_records)

        # Detection delay: cycles from attack start to first anomaly detection
        detection_delay = None
        for r in atk_records:
            if r.is_anomaly:
                detection_delay = r.cycle - attack.start_cycle
                break

        # Most common SHAP features for this attack
        feature_counts: Dict[str, int] = {}
        for r in detected_cycles:
            for f in r.top_features:
                fname = f["feature"]
                feature_counts[fname] = feature_counts.get(fname, 0) + 1
        top_indicators = sorted(feature_counts.items(), key=lambda x: -x[1])[:3]

        per_attack.append({
            "attack_id": attack.attack_id,
            "attack_type": attack.attack_type,
            "start_cycle": attack.start_cycle,
            "end_cycle": attack.end_cycle,
            "duration_cycles": n_total,
            "duration_s": attack.duration_s,
            "detected_cycles": n_detected,
            "detection_rate": n_detected / n_total if n_total > 0 else 0.0,
            "detection_delay_cycles": detection_delay,
            "detection_delay_s": detection_delay * INTERVAL_SECONDS if detection_delay is not None else None,
            "top_indicators": [{"feature": f, "count": c} for f, c in top_indicators],
        })

    # Per-type aggregate
    per_type: Dict[str, Dict] = {}
    for a in per_attack:
        atype = a["attack_type"]
        if atype not in per_type:
            per_type[atype] = {
                "total_attacks": 0,
                "total_cycles": 0,
                "detected_cycles": 0,
                "detection_delays": [],
                "feature_counts": {},
            }
        pt = per_type[atype]
        pt["total_attacks"] += 1
        pt["total_cycles"] += a["duration_cycles"]
        pt["detected_cycles"] += a["detected_cycles"]
        if a["detection_delay_cycles"] is not None:
            pt["detection_delays"].append(a["detection_delay_cycles"])
        for ind in a["top_indicators"]:
            fname = ind["feature"]
            pt["feature_counts"][fname] = pt["feature_counts"].get(fname, 0) + ind["count"]

    per_type_summary = {}
    for atype, data in per_type.items():
        delays = data["detection_delays"]
        fc = data["feature_counts"]
        top_feats = sorted(fc.items(), key=lambda x: -x[1])[:3]
        per_type_summary[atype] = {
            "total_attacks": data["total_attacks"],
            "total_cycles": data["total_cycles"],
            "detected_cycles": data["detected_cycles"],
            "detection_rate": data["detected_cycles"] / data["total_cycles"] if data["total_cycles"] > 0 else 0.0,
            "mean_detection_delay_cycles": np.mean(delays) if delays else None,
            "mean_detection_delay_s": np.mean(delays) * INTERVAL_SECONDS if delays else None,
            "attacks_detected": len(delays),
            "attacks_missed": data["total_attacks"] - len(delays),
            "top_indicators": [f for f, _ in top_feats],
        }

    # Mean anomaly scores
    atk_scores = [r.anomaly_score for r in attack_records if r.is_anomaly]
    normal_scores = [r.anomaly_score for r in normal_records]

    avg_detection_latency = np.mean([r.detection_latency_ms for r in records if r.detection_latency_ms > 0])

    return {
        "overview": {
            "total_cycles": total,
            "duration_s": total * INTERVAL_SECONDS,
            "duration_min": total * INTERVAL_SECONDS / 60,
            "normal_cycles": len(normal_records),
            "idle_cycles": len(idle_records),
            "attack_cycles": len(attack_records),
            "total_attacks": len(attacks),
        },
        "detection": {
            "true_positives": tp,
            "false_positives": total_fp,
            "false_positives_normal": fp_normal,
            "false_positives_idle": fp_idle,
            "true_negatives": tn,
            "false_negatives": fn,
            "precision": precision,
            "recall": recall,
            "f1_score": f1,
            "accuracy": accuracy,
            "false_positive_rate": fpr,
        },
        "scores": {
            "mean_attack_anomaly_score": float(np.mean(atk_scores)) if atk_scores else None,
            "std_attack_anomaly_score": float(np.std(atk_scores)) if atk_scores else None,
            "mean_normal_score": float(np.mean(normal_scores)) if normal_scores else None,
            "std_normal_score": float(np.std(normal_scores)) if normal_scores else None,
        },
        "latency": {
            "mean_detection_latency_ms": float(avg_detection_latency) if not np.isnan(avg_detection_latency) else 0,
        },
        "per_attack": per_attack,
        "per_type": per_type_summary,
    }


# ──────────────────────────────────────────────────────────────────────────
# Output writers
# ──────────────────────────────────────────────────────────────────────────

def write_event_log(records: List[CycleRecord], path: Path) -> None:
    """Write JSONL event log."""
    with open(path, "w") as f:
        for r in records:
            entry = {
                "cycle": r.cycle,
                "simulated_time_s": r.simulated_time_s,
                "timestamp": r.simulated_timestamp,
                "phase": r.phase,
                "attack_type": r.attack_type,
                "attack_id": r.attack_id,
                "is_anomaly": r.is_anomaly,
                "anomaly_score": round(r.anomaly_score, 6),
                "severity": r.severity,
                "detection_latency_ms": round(r.detection_latency_ms, 3),
                "top_features": r.top_features,
            }
            f.write(json.dumps(entry) + "\n")


def write_timeline_csv(records: List[CycleRecord], path: Path) -> None:
    """Write CSV timeline for plotting."""
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "cycle", "time_s", "time_min", "phase", "attack_type",
            "is_anomaly", "anomaly_score", "severity",
        ])
        for r in records:
            writer.writerow([
                r.cycle,
                r.simulated_time_s,
                round(r.simulated_time_s / 60, 2),
                r.phase,
                r.attack_type or "",
                int(r.is_anomaly),
                round(r.anomaly_score, 6),
                r.severity,
            ])


def write_attack_schedule(attacks: List[ScheduledAttack], path: Path) -> None:
    """Write attack schedule JSON."""
    schedule = []
    for a in attacks:
        schedule.append({
            "attack_id": a.attack_id,
            "attack_type": a.attack_type,
            "start_cycle": a.start_cycle,
            "end_cycle": a.end_cycle,
            "start_time_s": a.start_time_s,
            "end_time_s": a.end_time_s,
            "duration_s": a.duration_s,
            "duration_min": round(a.duration_s / 60, 1),
        })
    with open(path, "w") as f:
        json.dump({"attacks": schedule, "total_attacks": len(attacks)}, f, indent=2)


def write_summary(metrics: Dict, path: Path) -> None:
    """Write summary JSON."""

    # Convert numpy types to native Python for JSON serialization
    def _convert(obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, dict):
            return {k: _convert(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_convert(i) for i in obj]
        return obj

    with open(path, "w") as f:
        json.dump(_convert(metrics), f, indent=2)


def write_latex_tables(metrics: Dict, attacks: List[ScheduledAttack],
                       records: List[CycleRecord], path: Path) -> None:
    """Generate publication-ready LaTeX tables."""

    lines = []
    lines.append("% ====================================================================")
    lines.append("% Extended Simulation Results — Auto-generated LaTeX Tables")
    lines.append(f"% Generated: {datetime.now().isoformat()}")
    lines.append(f"% Duration: {metrics['overview']['duration_min']:.0f} minutes "
                 f"({metrics['overview']['total_cycles']} cycles)")
    lines.append("% ====================================================================")
    lines.append("")

    # ── Table 1: Overall Detection Performance ────────────────────────
    d = metrics["detection"]
    o = metrics["overview"]
    lines.append("% Table 1: Overall Detection Performance")
    lines.append("\\begin{table}[htbp]")
    lines.append("\\centering")
    lines.append("\\caption{Extended Simulation: Overall Detection Performance}")
    lines.append("\\label{tab:extended-overall}")
    lines.append("\\begin{tabular}{lr}")
    lines.append("\\toprule")
    lines.append("\\textbf{Metric} & \\textbf{Value} \\\\")
    lines.append("\\midrule")
    lines.append(f"Simulation Duration & {o['duration_min']:.0f} min ({o['total_cycles']} cycles) \\\\")
    lines.append(f"Total Attacks Scheduled & {o['total_attacks']} \\\\")
    lines.append(f"Normal Traffic Cycles & {o['normal_cycles']} \\\\")
    lines.append(f"Idle Traffic Cycles & {o['idle_cycles']} \\\\")
    lines.append(f"Attack Traffic Cycles & {o['attack_cycles']} \\\\")
    lines.append("\\midrule")
    lines.append(f"True Positives (TP) & {d['true_positives']} \\\\")
    lines.append(f"True Negatives (TN) & {d['true_negatives']} \\\\")
    lines.append(f"False Positives (FP) & {d['false_positives']} \\\\")
    lines.append(f"\\quad during Normal Traffic & {d['false_positives_normal']} \\\\")
    lines.append(f"\\quad during Idle Traffic & {d['false_positives_idle']} \\\\")
    lines.append(f"False Negatives (FN) & {d['false_negatives']} \\\\")
    lines.append("\\midrule")
    lines.append(f"Precision & {d['precision']:.4f} \\\\")
    lines.append(f"Recall & {d['recall']:.4f} \\\\")
    lines.append(f"F1-Score & {d['f1_score']:.4f} \\\\")
    lines.append(f"Accuracy & {d['accuracy']:.4f} \\\\")
    lines.append(f"False Positive Rate & {d['false_positive_rate']:.4f} \\\\")
    lines.append("\\midrule")
    s = metrics["scores"]
    lines.append(f"Mean Attack Anomaly Score & {s['mean_attack_anomaly_score']:.4f} \\\\") if s['mean_attack_anomaly_score'] else None
    lines.append(f"Mean Normal Traffic Score & {s['mean_normal_score']:.4f} \\\\") if s['mean_normal_score'] else None
    lines.append(f"Mean Detection Latency & {metrics['latency']['mean_detection_latency_ms']:.2f} ms \\\\")
    lines.append("\\bottomrule")
    lines.append("\\end{tabular}")
    lines.append("\\end{table}")
    lines.append("")

    # Filter out None entries from conditional appends
    lines = [l for l in lines if l is not None]

    # ── Table 2: Per-Attack-Type Detection Performance ────────────────
    lines.append("% Table 2: Detection Performance by Attack Type")
    lines.append("\\begin{table}[htbp]")
    lines.append("\\centering")
    lines.append("\\caption{Detection Performance by Attack Type}")
    lines.append("\\label{tab:extended-per-type}")
    lines.append("\\begin{tabular}{lccccl}")
    lines.append("\\toprule")
    lines.append("\\textbf{Attack Type} & \\textbf{Count} & \\textbf{Cycles} & "
                 "\\textbf{Det. Rate} & \\textbf{Avg Delay} & \\textbf{Top Indicators} \\\\")
    lines.append("\\midrule")

    pt = metrics["per_type"]
    for atype in sorted(pt.keys()):
        data = pt[atype]
        # Format attack type name
        name = atype.replace("_", " ").title()
        det_rate = f"{data['detection_rate']:.1%}"
        delay = f"{data['mean_detection_delay_s']:.0f}s" if data['mean_detection_delay_s'] is not None else "N/A"
        indicators = ", ".join(data["top_indicators"][:2]) if data["top_indicators"] else "---"
        # Escape underscores in feature names for LaTeX
        indicators = indicators.replace("_", "\\_")
        lines.append(f"{name} & {data['total_attacks']} & {data['total_cycles']} & "
                     f"{det_rate} & {delay} & {indicators} \\\\")

    lines.append("\\bottomrule")
    lines.append("\\end{tabular}")
    lines.append("\\end{table}")
    lines.append("")

    # ── Table 3: Individual Attack Results ────────────────────────────
    lines.append("% Table 3: Individual Attack Detection Results")
    lines.append("\\begin{table*}[htbp]")
    lines.append("\\centering")
    lines.append("\\caption{Individual Attack Detection Results (Extended Simulation)}")
    lines.append("\\label{tab:extended-attacks}")
    lines.append("\\begin{tabular}{clccccc}")
    lines.append("\\toprule")
    lines.append("\\textbf{\\#} & \\textbf{Attack Type} & \\textbf{Start} & "
                 "\\textbf{Duration} & \\textbf{Detected} & \\textbf{Rate} & "
                 "\\textbf{Delay} \\\\")
    lines.append("\\midrule")

    for a in metrics["per_attack"]:
        name = a["attack_type"].replace("_", " ").title()
        start_min = a["start_cycle"] * INTERVAL_SECONDS / 60
        dur_min = a["duration_s"] / 60
        det_frac = f"{a['detected_cycles']}/{a['duration_cycles']}"
        rate = f"{a['detection_rate']:.0%}"
        delay = f"{a['detection_delay_s']:.0f}s" if a['detection_delay_s'] is not None else "---"
        lines.append(f"{a['attack_id']+1} & {name} & {start_min:.1f}min & "
                     f"{dur_min:.1f}min & {det_frac} & {rate} & {delay} \\\\")

    lines.append("\\bottomrule")
    lines.append("\\end{tabular}")
    lines.append("\\end{table*}")
    lines.append("")

    # ── Table 4: False Positive Analysis ──────────────────────────────
    lines.append("% Table 4: False Positive Analysis")
    lines.append("\\begin{table}[htbp]")
    lines.append("\\centering")
    lines.append("\\caption{False Positive Analysis During Non-Attack Periods}")
    lines.append("\\label{tab:extended-fp}")
    lines.append("\\begin{tabular}{lcc}")
    lines.append("\\toprule")
    lines.append("\\textbf{Traffic Phase} & \\textbf{Total Cycles} & \\textbf{False Positives} \\\\")
    lines.append("\\midrule")
    lines.append(f"Normal Traffic & {o['normal_cycles']} & {d['false_positives_normal']} \\\\")
    lines.append(f"Idle Traffic (rate=0) & {o['idle_cycles']} & {d['false_positives_idle']} \\\\")
    lines.append("\\midrule")
    total_benign = o['normal_cycles'] + o['idle_cycles']
    lines.append(f"\\textbf{{Total Benign}} & \\textbf{{{total_benign}}} & "
                 f"\\textbf{{{d['false_positives']}}} \\\\")
    if total_benign > 0:
        lines.append(f"FP Rate & --- & {d['false_positives'] / total_benign:.4f} \\\\")
    lines.append("\\bottomrule")
    lines.append("\\end{tabular}")
    lines.append("\\end{table}")
    lines.append("")

    # ── Table 5: SHAP Feature Importance (aggregated) ─────────────────
    lines.append("% Table 5: Aggregated SHAP Feature Importance Across Detected Attacks")
    lines.append("\\begin{table}[htbp]")
    lines.append("\\centering")
    lines.append("\\caption{Top SHAP Features Across All Detected Attacks}")
    lines.append("\\label{tab:extended-shap}")
    lines.append("\\begin{tabular}{lcc}")
    lines.append("\\toprule")
    lines.append("\\textbf{Feature} & \\textbf{Appearances} & \\textbf{Mean |SHAP|} \\\\")
    lines.append("\\midrule")

    # Aggregate SHAP across all attack detections
    feature_stats: Dict[str, List[float]] = {}
    for r in records:
        if r.phase == "ATTACK" and r.is_anomaly and r.top_features:
            for f in r.top_features:
                fname = f["feature"]
                if fname not in feature_stats:
                    feature_stats[fname] = []
                feature_stats[fname].append(abs(f["shap_value"]))

    sorted_feats = sorted(feature_stats.items(), key=lambda x: -len(x[1]))
    for fname, vals in sorted_feats[:10]:
        latex_name = fname.replace("_", "\\_")
        lines.append(f"{latex_name} & {len(vals)} & {np.mean(vals):.4f} \\\\")

    lines.append("\\bottomrule")
    lines.append("\\end{tabular}")
    lines.append("\\end{table}")

    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


# ══════════════════════════════════════════════════════════════════════════
# Console summary
# ══════════════════════════════════════════════════════════════════════════

def print_summary(metrics: Dict, attacks: List[ScheduledAttack]) -> None:
    """Print a human-readable summary to the console."""
    o = metrics["overview"]
    d = metrics["detection"]
    s = metrics["scores"]

    width = 70
    print(f"\n{_BOLD}{_CYAN}{'=' * width}")
    print(f"  SIMULATION RESULTS SUMMARY")
    print(f"{'=' * width}{_RESET}\n")

    print(f"  {_BOLD}Overview{_RESET}")
    print(f"    Duration:        {o['duration_min']:.0f} minutes ({o['total_cycles']} cycles × {INTERVAL_SECONDS}s)")
    print(f"    Total attacks:   {o['total_attacks']}")
    print(f"    Attack types:    {len(metrics['per_type'])}")
    print(f"    Normal cycles:   {o['normal_cycles']}")
    print(f"    Idle cycles:     {o['idle_cycles']}")
    print(f"    Attack cycles:   {o['attack_cycles']}")

    print(f"\n  {_BOLD}Detection Performance{_RESET}")
    print(f"    Precision:       {d['precision']:.4f}")
    print(f"    Recall:          {d['recall']:.4f}")
    print(f"    F1-Score:        {d['f1_score']:.4f}")
    print(f"    Accuracy:        {d['accuracy']:.4f}")
    print(f"    FP Rate:         {d['false_positive_rate']:.4f}")

    print(f"\n  {_BOLD}Confusion Matrix{_RESET}")
    print(f"    TP: {d['true_positives']:4d}  │  FP: {d['false_positives']:4d}")
    print(f"    FN: {d['false_negatives']:4d}  │  TN: {d['true_negatives']:4d}")

    print(f"\n  {_BOLD}False Positive Breakdown{_RESET}")
    print(f"    During normal traffic:  {d['false_positives_normal']}")
    print(f"    During idle traffic:    {d['false_positives_idle']}")

    if s['mean_attack_anomaly_score'] is not None:
        print(f"\n  {_BOLD}Anomaly Scores{_RESET}")
        print(f"    Mean attack score:  {s['mean_attack_anomaly_score']:+.4f} ± {s['std_attack_anomaly_score']:.4f}")
        print(f"    Mean normal score:  {s['mean_normal_score']:+.4f} ± {s['std_normal_score']:.4f}")

    print(f"\n  {_BOLD}Per-Attack-Type Results{_RESET}")
    pt = metrics["per_type"]
    for atype in sorted(pt.keys()):
        data = pt[atype]
        name = atype.replace("_", " ").title()
        colour = _GREEN if data['detection_rate'] >= 0.8 else (_YELLOW if data['detection_rate'] >= 0.5 else _RED)
        delay_str = f"{data['mean_detection_delay_s']:.0f}s" if data['mean_detection_delay_s'] is not None else "N/A"
        print(f"    {name:.<30s} {colour}{data['detection_rate']:6.1%}{_RESET}  "
              f"({data['detected_cycles']}/{data['total_cycles']} cycles)  "
              f"delay={delay_str}")

    print(f"\n  {_BOLD}Individual Attacks{_RESET}")
    for a in metrics["per_attack"]:
        name = a["attack_type"].replace("_", " ").title()
        start_min = a["start_cycle"] * INTERVAL_SECONDS / 60
        colour = _GREEN if a['detection_rate'] >= 0.8 else (_YELLOW if a['detection_rate'] >= 0.5 else _RED)
        delay_str = f"{a['detection_delay_s']:.0f}s" if a['detection_delay_s'] is not None else "MISSED"
        status = "DETECTED" if a['detection_delay_s'] is not None else f"{_RED}MISSED{_RESET}"
        print(f"    #{a['attack_id']+1:2d}  {name:.<25s}  t={start_min:5.1f}min  "
              f"{colour}{a['detection_rate']:5.0%}{_RESET}  "
              f"({a['detected_cycles']}/{a['duration_cycles']})  "
              f"delay={delay_str}")

    print(f"\n  {_BOLD}Detection Latency{_RESET}")
    print(f"    Mean inference time: {metrics['latency']['mean_detection_latency_ms']:.2f} ms")

    print(f"\n{_BOLD}{_CYAN}{'=' * width}{_RESET}")


# ══════════════════════════════════════════════════════════════════════════
# Main entry point
# ══════════════════════════════════════════════════════════════════════════

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extended 1-hour DDoS detection simulation"
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED,
                        help="Random seed for reproducibility (default: 42)")
    parser.add_argument("--duration", type=int, default=DEFAULT_DURATION,
                        help="Simulation duration in seconds (default: 3600)")
    parser.add_argument("--real-time", action="store_true",
                        help="Run in real-time (15s between cycles)")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="Output directory (default: results/simulation_<timestamp>)")
    args = parser.parse_args()

    # Suppress noisy warnings
    warnings.filterwarnings("ignore", category=RuntimeWarning)
    warnings.filterwarnings("ignore", category=FutureWarning)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)-8s %(message)s")

    total_cycles = args.duration // INTERVAL_SECONDS
    rng = np.random.RandomState(args.seed)

    # ── Header ────────────────────────────────────────────────────────
    width = 70
    print(f"\n{_BOLD}{_CYAN}{'=' * width}")
    print(f"  EXTENDED SIMULATION — DDoS Detection Pipeline")
    print(f"{'=' * width}{_RESET}\n")
    print(f"  Duration:     {args.duration}s ({args.duration/60:.0f} min)")
    print(f"  Cycles:       {total_cycles} (every {INTERVAL_SECONDS}s)")
    print(f"  Seed:         {args.seed}")
    print(f"  Mode:         {'Real-time (15s interval)' if args.real_time else 'Fast (no delay)'}")
    print()

    # ── Output directory ──────────────────────────────────────────────
    if args.output_dir:
        out_dir = Path(args.output_dir)
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = Path("results") / f"simulation_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"  Output:       {out_dir.resolve()}\n")

    # ── Phase 1: Schedule attacks ─────────────────────────────────────
    print(f"{_BOLD}  Phase 1: Attack Scheduling{_RESET}")
    attacks = schedule_attacks(total_cycles, rng, INTERVAL_SECONDS)
    idle_periods = plan_idle_periods(total_cycles, attacks, rng)

    print(f"  {_GREEN}[OK]{_RESET}  Scheduled {len(attacks)} attacks across {args.duration/60:.0f} minutes")
    for a in attacks:
        start_min = a.start_time_s / 60
        dur_min = a.duration_s / 60
        name = a.attack_type.replace("_", " ").title()
        print(f"       #{a.attack_id+1:2d}  {name:.<30s}  "
              f"t={start_min:5.1f}min  dur={dur_min:.1f}min  "
              f"(cycles {a.start_cycle}-{a.end_cycle - 1})")

    print(f"  {_GREEN}[OK]{_RESET}  Planned {len(idle_periods)} idle periods (traffic drops to 0)")
    print()

    # ── Phase 2: Train model ──────────────────────────────────────────
    print(f"{_BOLD}  Phase 2: Model Training{_RESET}")
    with tempfile.TemporaryDirectory(prefix="ddos_sim_") as tmpdir:
        model_path = train_model(Path(tmpdir), args.seed)

        print(f"\n{_BOLD}  Phase 3: Loading Detector{_RESET}")
        detector = AnomalyDetector(model_path, enable_xai=True)
        print(f"  {_GREEN}[OK]{_RESET}  Detector ready (version: {detector.model_version})")
        print()

        # ── Phase 3: Run simulation ──────────────────────────────────
        print(f"{_BOLD}  Phase 4: Running Simulation ({total_cycles} cycles){_RESET}")
        print()

        records = run_simulation(
            detector=detector,
            total_cycles=total_cycles,
            attacks=attacks,
            idle_periods=idle_periods,
            seed=args.seed,
            real_time=args.real_time,
        )

    # ── Phase 4: Compute metrics ──────────────────────────────────────
    print(f"\n{_BOLD}  Phase 5: Computing Metrics{_RESET}")
    metrics = compute_metrics(records, attacks)

    # ── Phase 5: Write outputs ────────────────────────────────────────
    print(f"\n{_BOLD}  Phase 6: Writing Output Files{_RESET}")

    event_log_path = out_dir / "event_log.jsonl"
    write_event_log(records, event_log_path)
    print(f"  {_GREEN}[OK]{_RESET}  {event_log_path}")

    timeline_path = out_dir / "timeline.csv"
    write_timeline_csv(records, timeline_path)
    print(f"  {_GREEN}[OK]{_RESET}  {timeline_path}")

    schedule_path = out_dir / "attack_schedule.json"
    write_attack_schedule(attacks, schedule_path)
    print(f"  {_GREEN}[OK]{_RESET}  {schedule_path}")

    summary_path = out_dir / "summary.json"
    write_summary(metrics, summary_path)
    print(f"  {_GREEN}[OK]{_RESET}  {summary_path}")

    latex_path = out_dir / "latex_tables.tex"
    write_latex_tables(metrics, attacks, records, latex_path)
    print(f"  {_GREEN}[OK]{_RESET}  {latex_path}")

    # ── Print summary ─────────────────────────────────────────────────
    print_summary(metrics, attacks)

    # ── Exit ──────────────────────────────────────────────────────────
    d = metrics["detection"]
    if d["recall"] >= 0.7 and d["false_positive_rate"] <= 0.1:
        print(f"\n  {_GREEN}{_BOLD}SIMULATION PASSED — detection meets research thresholds{_RESET}")
        print(f"  {_GREEN}Recall ≥ 70%: {d['recall']:.1%}  |  FPR ≤ 10%: {d['false_positive_rate']:.1%}{_RESET}\n")
        return 0
    else:
        print(f"\n  {_YELLOW}{_BOLD}SIMULATION COMPLETE — review results{_RESET}")
        print(f"  {_YELLOW}Recall: {d['recall']:.1%}  |  FPR: {d['false_positive_rate']:.1%}{_RESET}\n")
        return 0


if __name__ == "__main__":
    sys.exit(main())
