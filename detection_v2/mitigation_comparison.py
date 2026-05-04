#!/usr/bin/env python3
"""
Mitigation Comparison Simulation
=================================

Simulates three crossfire DDoS mitigation strategies and compares
their effectiveness using the detection_v2 pipeline:

  1. **No Mitigation**     — Attack persists at full intensity.
  2. **Native K8s**        — Manual kubectl-based intervention after ~60-90 s,
                             static ~50-55 % impact reduction.
  3. **Nephio Automated**  — Automated detection + intent-based action in
                             ~30-45 s, progressive 70-85 % reduction with
                             drift correction.

This is a *local simulation* — it does NOT require a live Kubernetes
cluster.  It exercises the full detection_v2 pipeline (feature extraction,
model inference, SHAP explanation) against synthetic MetricSamples that
model the service-level impact of each mitigation strategy.

Output (under results/mitigation_comparison_<timestamp>/):
    comparison_table.md       Markdown comparison table
    comparison_data.json      Raw data for programmatic use
    scenario_<name>/          Per-scenario results
        event_log.jsonl
        timeline.csv
        summary.json
    latex_comparison.tex      Publication-ready LaTeX tables

Usage:
    python -m detection_v2.mitigation_comparison
    python -m detection_v2.mitigation_comparison --seed 42 --duration 1800
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
from copy import deepcopy
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from detection_v2.core.schema import MetricSample, Severity
from detection_v2.core.model import AnomalyDetector
from detection_v2.training.trainer import Trainer

# ══════════════════════════════════════════════════════════════════════════
# Constants
# ══════════════════════════════════════════════════════════════════════════

INTERVAL_SECONDS = 15
DEFAULT_DURATION = 1800  # 30 minutes — enough for meaningful comparison
DEFAULT_SEED = 42

ATTACK_TYPES = [
    "crossfire_link_flood",
    "volumetric_flood",
    "slowloris",
    "syn_flood",
]

# Terminal colours
_G = "\033[92m"
_R = "\033[91m"
_Y = "\033[93m"
_C = "\033[96m"
_B = "\033[1m"
_D = "\033[2m"
_0 = "\033[0m"


# ══════════════════════════════════════════════════════════════════════════
# Data structures
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class ScheduledAttack:
    attack_id: int
    attack_type: str
    start_cycle: int
    end_cycle: int       # exclusive
    duration_cycles: int
    start_time_s: float
    end_time_s: float
    duration_s: float


@dataclass
class CycleRecord:
    cycle: int
    time_s: float
    phase: str               # NORMAL | ATTACK | MITIGATING | RECOVERY
    attack_type: Optional[str]
    attack_id: Optional[int]
    is_anomaly: bool
    anomaly_score: float
    severity: str
    detection_latency_ms: float
    # Service-level metrics (from the MetricSample that was generated)
    request_rate: float
    latency_p50_ms: float
    latency_p99_ms: float
    error_rate: float
    byte_rate_in: float
    mitigation_effectiveness: float  # 0.0 = no mitigation, 1.0 = fully mitigated


@dataclass
class ScenarioResult:
    name: str
    records: List[CycleRecord]
    attacks: List[ScheduledAttack]
    metrics: Dict[str, Any] = field(default_factory=dict)


# ══════════════════════════════════════════════════════════════════════════
# Traffic generators (reused from extended_simulation patterns)
# ══════════════════════════════════════════════════════════════════════════

def _make_normal(rng: np.random.RandomState, ts: float,
                 variation: float = 0.5) -> MetricSample:
    """Generate a normal-traffic MetricSample."""
    diurnal = 0.85 + 0.3 * np.sin(np.pi * variation)
    base = 50.0 * diurnal
    return MetricSample(
        timestamp=ts,
        request_rate=max(0.0, base + rng.normal(0, 5)),
        request_rate_variance=max(0.0, 4.0 + rng.normal(0, 1)),
        latency_p50_ms=max(0.0, 12.0 + rng.normal(0, 2)),
        latency_p95_ms=max(0.0, 45.0 + rng.normal(0, 5)),
        latency_p99_ms=max(0.0, 120.0 + rng.normal(0, 10)),
        error_rate=max(0.0, 0.5 + rng.normal(0, 0.2)),
        total_request_rate=max(0.001, base + rng.normal(0, 5)),
        byte_rate_in=max(0.0, 25_000.0 * diurnal + rng.normal(0, 2000)),
        byte_rate_out=max(0.0, 125_000.0 * diurnal + rng.normal(0, 10_000)),
        avg_request_size_bytes=max(0.0, 500.0 + rng.normal(0, 50)),
        avg_response_size_bytes=max(0.0, 2500.0 + rng.normal(0, 200)),
        connection_open_rate=max(0.0, 8.0 + rng.normal(0, 1)),
        connection_close_rate=max(0.0, 7.5 + rng.normal(0, 1)),
    )


# ── Attack generators ────────────────────────────────────────────────────

def _make_crossfire(rng: np.random.RandomState, ts: float,
                    intensity: float = 1.0) -> MetricSample:
    """Crossfire / link-flood: byte-rate asymmetry + connection churn."""
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
        byte_rate_out=max(0.0, 60_000.0 + rng.normal(0, 8_000)),
        avg_request_size_bytes=max(0.0, 1200.0 + rng.normal(0, 100)),
        avg_response_size_bytes=max(0.0, 100.0 + rng.normal(0, 20)),
        connection_open_rate=max(0.0, 500.0 * intensity + rng.normal(0, 50)),
        connection_close_rate=max(0.0, 50.0 + rng.normal(0, 10)),
    )


def _make_volumetric(rng: np.random.RandomState, ts: float,
                     intensity: float = 1.0) -> MetricSample:
    """Volumetric DDoS: massive request rate."""
    rate = 5000.0 * intensity
    return MetricSample(
        timestamp=ts,
        request_rate=max(0.0, rate + rng.normal(0, 500 * intensity)),
        request_rate_variance=max(0.0, 2000.0 * intensity + rng.normal(0, 300)),
        latency_p50_ms=max(0.0, 500.0 + rng.normal(0, 100)),
        latency_p95_ms=max(0.0, 3000.0 + rng.normal(0, 500)),
        latency_p99_ms=max(0.0, 8000.0 + rng.normal(0, 1000)),
        error_rate=max(0.0, 400.0 * intensity + rng.normal(0, 50)),
        total_request_rate=max(0.001, rate + rng.normal(0, 500)),
        byte_rate_in=max(0.0, 5_000_000 * intensity + rng.normal(0, 500_000)),
        byte_rate_out=max(0.0, 500_000 + rng.normal(0, 50_000)),
        avg_request_size_bytes=max(0.0, 100.0 + rng.normal(0, 20)),
        avg_response_size_bytes=max(0.0, 50.0 + rng.normal(0, 10)),
        connection_open_rate=max(0.0, 2000 * intensity + rng.normal(0, 200)),
        connection_close_rate=max(0.0, 200.0 + rng.normal(0, 50)),
    )


def _make_slowloris(rng: np.random.RandomState, ts: float,
                    intensity: float = 1.0) -> MetricSample:
    """Slowloris: low request rate, extreme latency."""
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


def _make_syn_flood(rng: np.random.RandomState, ts: float,
                    intensity: float = 1.0) -> MetricSample:
    """SYN flood: extreme connection open rate."""
    return MetricSample(
        timestamp=ts,
        request_rate=max(0.0, 100.0 + rng.normal(0, 20)),
        request_rate_variance=max(0.0, 80.0 * intensity + rng.normal(0, 15)),
        latency_p50_ms=max(0.0, 300.0 + rng.normal(0, 50)),
        latency_p95_ms=max(0.0, 2000.0 + rng.normal(0, 300)),
        latency_p99_ms=max(0.0, 5000.0 + rng.normal(0, 800)),
        error_rate=max(0.0, 50.0 * intensity + rng.normal(0, 10)),
        total_request_rate=max(0.001, 100.0 + rng.normal(0, 20)),
        byte_rate_in=max(0.0, 200_000 * intensity + rng.normal(0, 30_000)),
        byte_rate_out=max(0.0, 20_000 + rng.normal(0, 3_000)),
        avg_request_size_bytes=max(0.0, 64.0 + rng.normal(0, 8)),
        avg_response_size_bytes=max(0.0, rng.uniform(0, 5)),
        connection_open_rate=max(0.0, 5000 * intensity + rng.normal(0, 500)),
        connection_close_rate=max(0.0, 10.0 + rng.normal(0, 3)),
    )


_ATTACK_GEN = {
    "crossfire_link_flood": _make_crossfire,
    "volumetric_flood": _make_volumetric,
    "slowloris": _make_slowloris,
    "syn_flood": _make_syn_flood,
}


def _lerp_sample(attack: MetricSample, normal: MetricSample,
                 t: float) -> MetricSample:
    """
    Linearly interpolate between an attack sample and a normal sample.

    ``t = 0`` → full attack, ``t = 1`` → fully normal (mitigated).
    This models mitigation effectiveness on service-level metrics.
    """
    t = max(0.0, min(1.0, t))

    def _mix(a: float, n: float) -> float:
        return a * (1.0 - t) + n * t

    return MetricSample(
        timestamp=attack.timestamp,
        request_rate=max(0.0, _mix(attack.request_rate, normal.request_rate)),
        request_rate_variance=max(0.0, _mix(attack.request_rate_variance, normal.request_rate_variance)),
        latency_p50_ms=max(0.0, _mix(attack.latency_p50_ms, normal.latency_p50_ms)),
        latency_p95_ms=max(0.0, _mix(attack.latency_p95_ms, normal.latency_p95_ms)),
        latency_p99_ms=max(0.0, _mix(attack.latency_p99_ms, normal.latency_p99_ms)),
        error_rate=max(0.0, _mix(attack.error_rate, normal.error_rate)),
        total_request_rate=max(0.001, _mix(attack.total_request_rate, normal.total_request_rate)),
        byte_rate_in=max(0.0, _mix(attack.byte_rate_in, normal.byte_rate_in)),
        byte_rate_out=max(0.0, _mix(attack.byte_rate_out, normal.byte_rate_out)),
        avg_request_size_bytes=max(0.0, _mix(attack.avg_request_size_bytes, normal.avg_request_size_bytes)),
        avg_response_size_bytes=max(0.0, _mix(attack.avg_response_size_bytes, normal.avg_response_size_bytes)),
        connection_open_rate=max(0.0, _mix(attack.connection_open_rate, normal.connection_open_rate)),
        connection_close_rate=max(0.0, _mix(attack.connection_close_rate, normal.connection_close_rate)),
    )


# ══════════════════════════════════════════════════════════════════════════
# Mitigation strategy models
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class MitigationStrategy:
    """
    Defines how a mitigation strategy responds to attacks.

    Attributes:
        name:                  Human-readable strategy label.
        reaction_delay_cycles: Cycles from attack start to mitigation onset.
        initial_effectiveness: How effective it is immediately (0-1).
        max_effectiveness:     Peak effectiveness after ramp-up (0-1).
        ramp_up_cycles:        Cycles to reach max_effectiveness (after
                               reaction_delay).
        has_drift_correction:  Whether the strategy adapts to changing
                               attack patterns.
        drift_improvement:     Per-cycle effectiveness bonus from drift
                               correction (added on top of ramp-up).
        recovery_cycles:       Cycles after attack ends before metrics
                               fully normalise.
        auto_revert:           Whether mitigation automatically reverts
                               when attack ends (vs staying in place).
    """
    name: str
    reaction_delay_cycles: int
    initial_effectiveness: float
    max_effectiveness: float
    ramp_up_cycles: int
    has_drift_correction: bool
    drift_improvement: float
    recovery_cycles: int
    auto_revert: bool


NO_MITIGATION = MitigationStrategy(
    name="No Mitigation",
    reaction_delay_cycles=999,  # never kicks in
    initial_effectiveness=0.0,
    max_effectiveness=0.0,
    ramp_up_cycles=0,
    has_drift_correction=False,
    drift_improvement=0.0,
    recovery_cycles=2,
    auto_revert=True,
)

NATIVE_K8S = MitigationStrategy(
    name="Native K8s",
    reaction_delay_cycles=5,   # ~75 s (human reaction + manual kubectl)
    initial_effectiveness=0.30,
    max_effectiveness=0.55,
    ramp_up_cycles=4,          # stabilises over ~60 s
    has_drift_correction=False,
    drift_improvement=0.0,
    recovery_cycles=4,         # slower teardown (manual cleanup)
    auto_revert=False,         # must be manually reverted
)

NEPHIO_AUTOMATED = MitigationStrategy(
    name="Nephio Automated",
    reaction_delay_cycles=2,   # ~30 s (automated detection + intent action)
    initial_effectiveness=0.45,
    max_effectiveness=0.85,
    ramp_up_cycles=3,          # multi-layered mitigation applied progressively
    has_drift_correction=True,
    drift_improvement=0.02,    # 2% per-cycle improvement via drift correction
    recovery_cycles=2,         # automatic TTL-based revert
    auto_revert=True,
)


def compute_effectiveness(
    strategy: MitigationStrategy,
    cycles_since_attack_start: int,
    cycles_since_attack_end: Optional[int] = None,
    attack_duration_cycles: int = 0,
) -> float:
    """
    Compute current mitigation effectiveness for the given strategy.

    Returns a value in [0.0, 1.0] where 0 = no mitigation, 1 = fully
    mitigated (service back to normal).
    """
    # If this is "no mitigation", always return 0
    if strategy.max_effectiveness == 0.0:
        return 0.0

    # During the reaction delay, no mitigation is active yet
    if cycles_since_attack_start < strategy.reaction_delay_cycles:
        return 0.0

    active_cycles = cycles_since_attack_start - strategy.reaction_delay_cycles

    # Ramp from initial to max effectiveness
    if strategy.ramp_up_cycles > 0 and active_cycles < strategy.ramp_up_cycles:
        progress = active_cycles / strategy.ramp_up_cycles
        # Ease-in curve for more realistic ramp
        progress = progress ** 0.7
        eff = strategy.initial_effectiveness + (
            strategy.max_effectiveness - strategy.initial_effectiveness
        ) * progress
    else:
        eff = strategy.max_effectiveness

    # Drift correction: extra per-cycle improvement beyond ramp-up
    if strategy.has_drift_correction and active_cycles > strategy.ramp_up_cycles:
        extra_cycles = active_cycles - strategy.ramp_up_cycles
        eff = min(0.95, eff + strategy.drift_improvement * extra_cycles)

    return min(0.95, max(0.0, eff))


# ══════════════════════════════════════════════════════════════════════════
# Attack scheduler
# ══════════════════════════════════════════════════════════════════════════

def schedule_attacks(
    total_cycles: int,
    rng: np.random.RandomState,
) -> List[ScheduledAttack]:
    """
    Create a deterministic, non-overlapping attack schedule.

    For the comparison to be fair, every scenario faces the same attacks.
    """
    warmup = max(6, total_cycles // 12)
    cooldown = 4
    attacks: List[ScheduledAttack] = []
    cursor = warmup
    aid = 0

    # Target 4-8 attacks depending on duration
    target = min(8, max(4, total_cycles // 20))

    for _ in range(target):
        dur = rng.randint(6, min(16, total_cycles // 6))
        gap = rng.randint(6, min(20, total_cycles // 8))
        if cursor + dur >= total_cycles - cooldown:
            break
        atype = ATTACK_TYPES[aid % len(ATTACK_TYPES)]
        attacks.append(ScheduledAttack(
            attack_id=aid,
            attack_type=atype,
            start_cycle=cursor,
            end_cycle=cursor + dur,
            duration_cycles=dur,
            start_time_s=cursor * INTERVAL_SECONDS,
            end_time_s=(cursor + dur) * INTERVAL_SECONDS,
            duration_s=dur * INTERVAL_SECONDS,
        ))
        aid += 1
        cursor += dur + gap

    return attacks


# ══════════════════════════════════════════════════════════════════════════
# Simulation core
# ══════════════════════════════════════════════════════════════════════════

def run_scenario(
    scenario_name: str,
    strategy: MitigationStrategy,
    detector: AnomalyDetector,
    attacks: List[ScheduledAttack],
    total_cycles: int,
    seed: int,
) -> ScenarioResult:
    """
    Run one complete scenario simulation.

    Same attack schedule for every scenario — only the mitigation
    response differs.
    """
    rng = np.random.RandomState(seed + 200)
    base_time = time.time()
    records: List[CycleRecord] = []

    # Build attack lookup
    attack_map: Dict[int, ScheduledAttack] = {}
    for a in attacks:
        for c in range(a.start_cycle, a.end_cycle):
            attack_map[c] = a

    # Recovery tracking — cycles after each attack ends
    recovery_end: Dict[int, int] = {}
    for a in attacks:
        recovery_end[a.end_cycle] = a.end_cycle + strategy.recovery_cycles

    # Consecutive-anomaly tracking (mirrors ContinuousMonitor logic)
    consecutive_anomalies = 0
    consecutive_threshold = 2

    bar_width = 40

    for cycle in range(total_cycles):
        ts = base_time + cycle * INTERVAL_SECONDS
        variation = cycle / total_cycles

        # ── Determine phase and mitigation effectiveness ──────────
        if cycle in attack_map:
            atk = attack_map[cycle]
            cycles_in = cycle - atk.start_cycle
            eff = compute_effectiveness(
                strategy, cycles_in,
                attack_duration_cycles=atk.duration_cycles,
            )
            phase = "MITIGATING" if eff > 0.05 else "ATTACK"
            attack_type = atk.attack_type
            attack_id = atk.attack_id
        else:
            # Check if we're in a post-attack recovery window
            in_recovery = False
            for a in attacks:
                if a.end_cycle <= cycle < a.end_cycle + strategy.recovery_cycles:
                    in_recovery = True
                    break

            if in_recovery:
                phase = "RECOVERY"
                eff = 0.0
            else:
                phase = "NORMAL"
                eff = 0.0
            attack_type = None
            attack_id = None

        # ── Generate sample ───────────────────────────────────────
        if phase == "NORMAL":
            sample = _make_normal(rng, ts, variation)
        elif phase == "RECOVERY":
            # Decaying perturbation during recovery
            normal_s = _make_normal(rng, ts, variation)
            # Slightly elevated metrics that decay toward normal
            sample = MetricSample(
                timestamp=ts,
                request_rate=max(0.0, normal_s.request_rate * (1.0 + rng.uniform(0, 0.1))),
                request_rate_variance=max(0.0, normal_s.request_rate_variance * (1.0 + rng.uniform(0, 0.2))),
                latency_p50_ms=max(0.0, normal_s.latency_p50_ms * (1.0 + rng.uniform(0, 0.15))),
                latency_p95_ms=max(0.0, normal_s.latency_p95_ms * (1.0 + rng.uniform(0, 0.15))),
                latency_p99_ms=max(0.0, normal_s.latency_p99_ms * (1.0 + rng.uniform(0, 0.15))),
                error_rate=max(0.0, normal_s.error_rate * (1.0 + rng.uniform(0, 0.1))),
                total_request_rate=max(0.001, normal_s.total_request_rate * (1.0 + rng.uniform(0, 0.1))),
                byte_rate_in=max(0.0, normal_s.byte_rate_in * (1.0 + rng.uniform(0, 0.1))),
                byte_rate_out=max(0.0, normal_s.byte_rate_out * (1.0 + rng.uniform(0, 0.1))),
                avg_request_size_bytes=max(0.0, normal_s.avg_request_size_bytes),
                avg_response_size_bytes=max(0.0, normal_s.avg_response_size_bytes),
                connection_open_rate=max(0.0, normal_s.connection_open_rate * (1.0 + rng.uniform(0, 0.1))),
                connection_close_rate=max(0.0, normal_s.connection_close_rate * (1.0 + rng.uniform(0, 0.1))),
            )
        else:
            # Attack or mitigating — generate full attack, then lerp
            atk_gen = _ATTACK_GEN[attack_type]
            # Intensity ramps with attack progress
            atk = attack_map[cycle]
            progress = (cycle - atk.start_cycle) / max(1, atk.duration_cycles - 1)
            intensity = 0.6 + 0.6 * np.sin(np.pi * progress)
            attack_sample = atk_gen(rng, ts, intensity)
            normal_sample = _make_normal(rng, ts, variation)

            if eff > 0.0:
                sample = _lerp_sample(attack_sample, normal_sample, eff)
            else:
                sample = attack_sample

        # ── Run detection ─────────────────────────────────────────
        if sample.request_rate < 1.0:
            is_anomaly = False
            score = 0.0
            severity = "NORMAL"
            det_latency = 0.0
            consecutive_anomalies = 0
        else:
            try:
                result = detector.detect(sample, explain=False)
                raw_anomaly = result.is_anomaly
                score = result.anomaly_score
                severity = result.severity.value
                det_latency = result.detection_latency_ms

                if raw_anomaly:
                    consecutive_anomalies += 1
                    is_anomaly = consecutive_anomalies >= consecutive_threshold
                else:
                    consecutive_anomalies = 0
                    is_anomaly = False
            except Exception:
                is_anomaly = False
                score = 0.0
                severity = "NORMAL"
                det_latency = 0.0

        records.append(CycleRecord(
            cycle=cycle,
            time_s=cycle * INTERVAL_SECONDS,
            phase=phase,
            attack_type=attack_type,
            attack_id=attack_id,
            is_anomaly=is_anomaly,
            anomaly_score=score,
            severity=severity,
            detection_latency_ms=det_latency,
            request_rate=sample.request_rate,
            latency_p50_ms=sample.latency_p50_ms,
            latency_p99_ms=sample.latency_p99_ms,
            error_rate=sample.error_rate,
            byte_rate_in=sample.byte_rate_in,
            mitigation_effectiveness=eff,
        ))

        # ── Console progress ──────────────────────────────────────
        filled = int(bar_width * (cycle + 1) / total_cycles)
        bar = "█" * filled + "░" * (bar_width - filled)
        if phase in ("ATTACK", "MITIGATING"):
            ps = f"{_R}{phase:.<12s}{_0}"
            ds = f"{_R}ANOM{_0}" if is_anomaly else f"{_Y}----{_0}"
        elif phase == "RECOVERY":
            ps = f"{_Y}RECOVERY...{_0} "
            ds = f"{_G}OK{_0}  " if not is_anomaly else f"{_R}FP{_0}  "
        else:
            ps = f"{_G}NORMAL......{_0}"
            ds = f"{_G}OK{_0}  " if not is_anomaly else f"{_R}FP{_0}  "

        eff_str = f"eff={eff:.0%}" if eff > 0 else "     "
        print(
            f"\r  [{bar}] {cycle+1:3d}/{total_cycles}  "
            f"{ps} {ds} score={score:+.3f} {eff_str}",
            end="", flush=True,
        )

    print()  # newline after progress
    return ScenarioResult(name=scenario_name, records=records, attacks=attacks)


# ══════════════════════════════════════════════════════════════════════════
# Metrics computation
# ══════════════════════════════════════════════════════════════════════════

def compute_scenario_metrics(result: ScenarioResult) -> Dict[str, Any]:
    """Compute comprehensive metrics for one scenario."""
    records = result.records
    attacks = result.attacks

    # Partition records
    normal_recs = [r for r in records if r.phase == "NORMAL"]
    attack_recs = [r for r in records if r.phase in ("ATTACK", "MITIGATING")]
    recovery_recs = [r for r in records if r.phase == "RECOVERY"]

    # ── Detection metrics ─────────────────────────────────────────
    tp = sum(1 for r in attack_recs if r.is_anomaly)
    fn = sum(1 for r in attack_recs if not r.is_anomaly)
    fp = sum(1 for r in normal_recs + recovery_recs if r.is_anomaly)
    tn = sum(1 for r in normal_recs + recovery_recs if not r.is_anomaly)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    accuracy = (tp + tn) / len(records) if records else 0.0

    # ── Service-level metrics during attack periods ───────────────
    if attack_recs:
        avg_latency_attack = np.mean([r.latency_p50_ms for r in attack_recs])
        avg_p99_attack = np.mean([r.latency_p99_ms for r in attack_recs])
        avg_error_attack = np.mean([r.error_rate for r in attack_recs])
        avg_byte_in_attack = np.mean([r.byte_rate_in for r in attack_recs])
        avg_req_rate_attack = np.mean([r.request_rate for r in attack_recs])
        avg_effectiveness = np.mean([r.mitigation_effectiveness for r in attack_recs])
        max_effectiveness = max(r.mitigation_effectiveness for r in attack_recs)
    else:
        avg_latency_attack = 0.0
        avg_p99_attack = 0.0
        avg_error_attack = 0.0
        avg_byte_in_attack = 0.0
        avg_req_rate_attack = 0.0
        avg_effectiveness = 0.0
        max_effectiveness = 0.0

    # ── Service-level metrics during normal periods ───────────────
    if normal_recs:
        avg_latency_normal = np.mean([r.latency_p50_ms for r in normal_recs])
        avg_p99_normal = np.mean([r.latency_p99_ms for r in normal_recs])
        avg_error_normal = np.mean([r.error_rate for r in normal_recs])
        avg_req_rate_normal = np.mean([r.request_rate for r in normal_recs])
    else:
        avg_latency_normal = 0.0
        avg_p99_normal = 0.0
        avg_error_normal = 0.0
        avg_req_rate_normal = 0.0

    # ── Per-attack analysis ───────────────────────────────────────
    per_attack = []
    for a in attacks:
        arecs = [r for r in records if r.cycle >= a.start_cycle and r.cycle < a.end_cycle]
        detected = [r for r in arecs if r.is_anomaly]

        # Time to first detection
        detection_delay = None
        for r in arecs:
            if r.is_anomaly:
                detection_delay = (r.cycle - a.start_cycle) * INTERVAL_SECONDS
                break

        # Time to mitigation becoming effective (eff > 0.1)
        mitigation_delay = None
        for r in arecs:
            if r.mitigation_effectiveness > 0.1:
                mitigation_delay = (r.cycle - a.start_cycle) * INTERVAL_SECONDS
                break

        avg_eff = np.mean([r.mitigation_effectiveness for r in arecs]) if arecs else 0.0

        per_attack.append({
            "attack_id": a.attack_id,
            "attack_type": a.attack_type,
            "duration_s": a.duration_s,
            "detection_delay_s": detection_delay,
            "mitigation_delay_s": mitigation_delay,
            "detection_rate": len(detected) / len(arecs) if arecs else 0.0,
            "avg_effectiveness": float(avg_eff),
            "avg_latency_p50_ms": float(np.mean([r.latency_p50_ms for r in arecs])) if arecs else 0.0,
            "avg_latency_p99_ms": float(np.mean([r.latency_p99_ms for r in arecs])) if arecs else 0.0,
            "avg_error_rate": float(np.mean([r.error_rate for r in arecs])) if arecs else 0.0,
        })

    # ── Overall service health score ──────────────────────────────
    # Fraction of cycles where service is considered "healthy":
    #   normal phase, or mitigating with effectiveness > 0.5
    healthy = sum(1 for r in records
                  if r.phase == "NORMAL"
                  or r.phase == "RECOVERY"
                  or (r.phase == "MITIGATING" and r.mitigation_effectiveness > 0.5))
    service_availability = healthy / len(records) if records else 0.0

    # ── Mean time to mitigation ───────────────────────────────────
    mit_delays = [a["mitigation_delay_s"] for a in per_attack
                  if a["mitigation_delay_s"] is not None]
    mean_ttm = np.mean(mit_delays) if mit_delays else None

    det_delays = [a["detection_delay_s"] for a in per_attack
                  if a["detection_delay_s"] is not None]
    mean_ttd = np.mean(det_delays) if det_delays else None

    metrics = {
        "scenario": result.name,
        "overview": {
            "total_cycles": len(records),
            "normal_cycles": len(normal_recs),
            "attack_cycles": len(attack_recs),
            "recovery_cycles": len(recovery_recs),
            "total_attacks": len(attacks),
        },
        "detection": {
            "tp": tp, "fp": fp, "tn": tn, "fn": fn,
            "precision": precision,
            "recall": recall,
            "f1_score": f1,
            "accuracy": accuracy,
        },
        "service_during_attack": {
            "avg_latency_p50_ms": float(avg_latency_attack),
            "avg_latency_p99_ms": float(avg_p99_attack),
            "avg_error_rate": float(avg_error_attack),
            "avg_byte_rate_in": float(avg_byte_in_attack),
            "avg_request_rate": float(avg_req_rate_attack),
            "avg_mitigation_effectiveness": float(avg_effectiveness),
            "max_mitigation_effectiveness": float(max_effectiveness),
        },
        "service_normal": {
            "avg_latency_p50_ms": float(avg_latency_normal),
            "avg_latency_p99_ms": float(avg_p99_normal),
            "avg_error_rate": float(avg_error_normal),
            "avg_request_rate": float(avg_req_rate_normal),
        },
        "response_time": {
            "mean_time_to_detection_s": float(mean_ttd) if mean_ttd else None,
            "mean_time_to_mitigation_s": float(mean_ttm) if mean_ttm else None,
        },
        "service_availability": float(service_availability),
        "per_attack": per_attack,
    }
    result.metrics = metrics
    return metrics


# ══════════════════════════════════════════════════════════════════════════
# Comparison & output
# ══════════════════════════════════════════════════════════════════════════

def _pct_improvement(baseline: float, improved: float,
                     lower_is_better: bool = True) -> float:
    if baseline == 0:
        return 0.0
    if lower_is_better:
        return ((baseline - improved) / baseline) * 100
    return ((improved - baseline) / baseline) * 100


def generate_comparison_table(results: List[ScenarioResult]) -> str:
    """Generate a Markdown comparison table from scenario results."""
    lines = []
    lines.append("# Crossfire DDoS Mitigation — Comparison Results\n")
    lines.append(f"_Generated: {datetime.now().isoformat()}_\n")

    # ── Table 1: Service-level impact during attacks ──────────────
    lines.append("## 1. Service Impact During Attack Periods\n")
    lines.append("| Metric | " + " | ".join(r.name for r in results) + " |")
    lines.append("|---|" + "|".join("---" for _ in results) + "|")

    metric_rows = [
        ("Avg Latency p50 (ms)", "service_during_attack", "avg_latency_p50_ms", True),
        ("Avg Latency p99 (ms)", "service_during_attack", "avg_latency_p99_ms", True),
        ("Avg Error Rate", "service_during_attack", "avg_error_rate", True),
        ("Avg Byte Rate In", "service_during_attack", "avg_byte_rate_in", True),
        ("Avg Request Rate", "service_during_attack", "avg_request_rate", False),
        ("Avg Mitigation Eff.", "service_during_attack", "avg_mitigation_effectiveness", False),
        ("Max Mitigation Eff.", "service_during_attack", "max_mitigation_effectiveness", False),
    ]

    for label, section, key, lower_is_better in metric_rows:
        vals = [r.metrics[section][key] for r in results]
        cells = []
        for v in vals:
            if isinstance(v, float) and v < 1:
                cells.append(f"{v:.3f}")
            elif isinstance(v, float) and v < 100:
                cells.append(f"{v:.1f}")
            else:
                cells.append(f"{v:,.0f}")
        lines.append(f"| {label} | " + " | ".join(cells) + " |")

    lines.append("")

    # ── Table 2: Response times ───────────────────────────────────
    lines.append("## 2. Response Times\n")
    lines.append("| Metric | " + " | ".join(r.name for r in results) + " |")
    lines.append("|---|" + "|".join("---" for _ in results) + "|")

    for label, key in [
        ("Mean Time to Detection (s)", "mean_time_to_detection_s"),
        ("Mean Time to Mitigation (s)", "mean_time_to_mitigation_s"),
    ]:
        vals = []
        for r in results:
            v = r.metrics["response_time"][key]
            vals.append(f"{v:.1f}" if v is not None else "N/A")
        lines.append(f"| {label} | " + " | ".join(vals) + " |")

    lines.append(f"| Service Availability | " +
                 " | ".join(f"{r.metrics['service_availability']:.1%}" for r in results) +
                 " |")
    lines.append("")

    # ── Table 3: Detection performance ────────────────────────────
    lines.append("## 3. Detection Performance\n")
    lines.append("| Metric | " + " | ".join(r.name for r in results) + " |")
    lines.append("|---|" + "|".join("---" for _ in results) + "|")

    for label, key in [
        ("Precision", "precision"),
        ("Recall", "recall"),
        ("F1-Score", "f1_score"),
        ("Accuracy", "accuracy"),
    ]:
        vals = [f"{r.metrics['detection'][key]:.3f}" for r in results]
        lines.append(f"| {label} | " + " | ".join(vals) + " |")

    lines.append("")

    # ── Table 4: Improvement analysis ─────────────────────────────
    if len(results) >= 3:
        no_mit = results[0].metrics
        native = results[1].metrics
        nephio = results[2].metrics

        lines.append("## 4. Nephio Improvement Analysis\n")

        lines.append("### vs No Mitigation\n")
        lines.append("| Metric | No Mitigation | Nephio | Improvement |")
        lines.append("|---|---|---|---|")

        comparisons = [
            ("Avg Latency p50 (ms)", "service_during_attack", "avg_latency_p50_ms", True),
            ("Avg Latency p99 (ms)", "service_during_attack", "avg_latency_p99_ms", True),
            ("Avg Error Rate", "service_during_attack", "avg_error_rate", True),
            ("Service Availability", None, "service_availability", False),
        ]
        for label, section, key, lower_better in comparisons:
            if section:
                b = no_mit[section][key]
                n = nephio[section][key]
            else:
                b = no_mit[key]
                n = nephio[key]
            imp = _pct_improvement(b, n, lower_better)
            lines.append(f"| {label} | {b:.2f} | {n:.2f} | {imp:+.1f}% |")

        lines.append("")
        lines.append("### vs Native K8s\n")
        lines.append("| Metric | Native K8s | Nephio | Improvement |")
        lines.append("|---|---|---|---|")

        for label, section, key, lower_better in comparisons:
            if section:
                b = native[section][key]
                n = nephio[section][key]
            else:
                b = native[key]
                n = nephio[key]
            imp = _pct_improvement(b, n, lower_better)
            lines.append(f"| {label} | {b:.2f} | {n:.2f} | {imp:+.1f}% |")

        lines.append("")

    # ── Table 5: Per-attack breakdown ─────────────────────────────
    lines.append("## 5. Per-Attack Breakdown\n")
    for r in results:
        lines.append(f"### {r.name}\n")
        lines.append("| # | Type | Duration | Det. Delay | Mit. Delay | Det. Rate | Avg Eff. | Avg p50 | Avg Err |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for a in r.metrics["per_attack"]:
            dd = f"{a['detection_delay_s']:.0f}s" if a["detection_delay_s"] is not None else "---"
            md = f"{a['mitigation_delay_s']:.0f}s" if a["mitigation_delay_s"] is not None else "---"
            lines.append(
                f"| {a['attack_id']+1} "
                f"| {a['attack_type']} "
                f"| {a['duration_s']:.0f}s "
                f"| {dd} "
                f"| {md} "
                f"| {a['detection_rate']:.0%} "
                f"| {a['avg_effectiveness']:.0%} "
                f"| {a['avg_latency_p50_ms']:.1f} "
                f"| {a['avg_error_rate']:.1f} |"
            )
        lines.append("")

    return "\n".join(lines)


def generate_latex_comparison(results: List[ScenarioResult]) -> str:
    """Generate publication-ready LaTeX comparison tables."""
    lines = []
    lines.append("% ====================================================================")
    lines.append("% Mitigation Comparison Results — Auto-generated LaTeX Tables")
    lines.append(f"% Generated: {datetime.now().isoformat()}")
    lines.append("% ====================================================================")
    lines.append("")

    # Table 1: Service Impact Comparison
    lines.append("\\begin{table}[htbp]")
    lines.append("\\centering")
    lines.append("\\caption{Service Impact During Attack Periods Under Different Mitigation Strategies}")
    lines.append("\\label{tab:mitigation-comparison}")
    lines.append("\\begin{tabular}{l" + "r" * len(results) + "}")
    lines.append("\\toprule")
    headers = " & ".join(f"\\textbf{{{r.name}}}" for r in results)
    lines.append(f"\\textbf{{Metric}} & {headers} \\\\")
    lines.append("\\midrule")

    rows = [
        ("Latency p50 (ms)", "service_during_attack", "avg_latency_p50_ms"),
        ("Latency p99 (ms)", "service_during_attack", "avg_latency_p99_ms"),
        ("Error Rate", "service_during_attack", "avg_error_rate"),
        ("Mitigation Eff.", "service_during_attack", "avg_mitigation_effectiveness"),
    ]
    for label, section, key in rows:
        vals = " & ".join(f"{r.metrics[section][key]:.1f}" for r in results)
        lines.append(f"{label} & {vals} \\\\")

    lines.append("\\midrule")
    vals = " & ".join(
        f"{r.metrics['response_time']['mean_time_to_mitigation_s']:.1f}s"
        if r.metrics['response_time']['mean_time_to_mitigation_s'] is not None
        else "N/A"
        for r in results
    )
    lines.append(f"Time to Mitigation & {vals} \\\\")

    vals = " & ".join(f"{r.metrics['service_availability']:.1%}" for r in results)
    lines.append(f"Service Availability & {vals} \\\\")

    lines.append("\\bottomrule")
    lines.append("\\end{tabular}")
    lines.append("\\end{table}")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
# File writers
# ══════════════════════════════════════════════════════════════════════════

def write_scenario_outputs(result: ScenarioResult, out_dir: Path) -> None:
    """Write per-scenario output files."""
    out_dir.mkdir(parents=True, exist_ok=True)

    # Event log (JSONL)
    with open(out_dir / "event_log.jsonl", "w") as f:
        for r in result.records:
            f.write(json.dumps(asdict(r)) + "\n")

    # Timeline CSV
    with open(out_dir / "timeline.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "cycle", "time_s", "time_min", "phase", "attack_type",
            "is_anomaly", "anomaly_score", "severity",
            "latency_p50", "latency_p99", "error_rate",
            "byte_rate_in", "request_rate", "mitigation_effectiveness",
        ])
        for r in result.records:
            writer.writerow([
                r.cycle, r.time_s, round(r.time_s / 60, 2),
                r.phase, r.attack_type or "",
                int(r.is_anomaly), round(r.anomaly_score, 6), r.severity,
                round(r.latency_p50_ms, 1), round(r.latency_p99_ms, 1),
                round(r.error_rate, 2), round(r.byte_rate_in, 0),
                round(r.request_rate, 1), round(r.mitigation_effectiveness, 3),
            ])

    # Summary JSON
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

    with open(out_dir / "summary.json", "w") as f:
        json.dump(_convert(result.metrics), f, indent=2)


# ══════════════════════════════════════════════════════════════════════════
# Training
# ══════════════════════════════════════════════════════════════════════════

def train_model(model_dir: Path, seed: int) -> Path:
    """Train IsolationForest on synthetic normal + attack traffic."""
    rng = np.random.RandomState(seed)

    print(f"  {_C}[..]{_0}  Generating training samples ...")
    normal_samples = []
    for i in range(800):
        normal_samples.append(_make_normal(rng, time.time() + i, i / 800.0))

    attack_rng = np.random.RandomState(seed + 1)
    attack_samples = []
    types = list(_ATTACK_GEN.keys())
    for i in range(100):
        gen = _ATTACK_GEN[types[i % len(types)]]
        attack_samples.append(gen(attack_rng, time.time() + i))

    print(f"  {_C}[..]{_0}  Training Isolation Forest (200 estimators) ...")
    trainer = Trainer(
        n_estimators=200,
        max_samples=256,
        shap_background_size=100,
        contamination=0.05,
    )
    results = trainer.fit(normal_samples, attack_samples=attack_samples)

    model_path = model_dir / "comparison_model.pkl"
    trainer.save(model_path)

    print(f"  {_G}[OK]{_0}  Model saved → {model_path}")
    print(f"  {_G}[OK]{_0}  Accuracy: {results['accuracy']:.1%}  "
          f"Recall: {results['attack_recall']:.1%}")
    return model_path


# ══════════════════════════════════════════════════════════════════════════
# Console summary
# ══════════════════════════════════════════════════════════════════════════

def print_comparison_summary(results: List[ScenarioResult]) -> None:
    """Print a human-readable comparison summary."""
    w = 76
    print(f"\n{_B}{_C}{'=' * w}")
    print(f"  MITIGATION COMPARISON — SUMMARY")
    print(f"{'=' * w}{_0}\n")

    # Header
    name_width = 22
    col_width = 14
    header = f"  {'Metric':<30s}"
    for r in results:
        header += f"{r.name:>{col_width}s}"
    print(f"{_B}{header}{_0}")
    print(f"  {'─' * (30 + col_width * len(results))}")

    # Service metrics during attack
    rows = [
        ("Avg Latency p50 (ms)", "service_during_attack", "avg_latency_p50_ms", ".1f"),
        ("Avg Latency p99 (ms)", "service_during_attack", "avg_latency_p99_ms", ".0f"),
        ("Avg Error Rate", "service_during_attack", "avg_error_rate", ".1f"),
        ("Mitigation Eff. (avg)", "service_during_attack", "avg_mitigation_effectiveness", ".0%"),
        ("Mitigation Eff. (max)", "service_during_attack", "max_mitigation_effectiveness", ".0%"),
    ]
    for label, section, key, fmt in rows:
        row = f"  {label:<30s}"
        for r in results:
            v = r.metrics[section][key]
            row += f"{v:{col_width}{fmt}}"
        print(row)

    print(f"  {'─' * (30 + col_width * len(results))}")

    # Response times
    for label, key in [
        ("Time to Detection (s)", "mean_time_to_detection_s"),
        ("Time to Mitigation (s)", "mean_time_to_mitigation_s"),
    ]:
        row = f"  {label:<30s}"
        for r in results:
            v = r.metrics["response_time"][key]
            row += f"{(str(round(v, 1)) + 's') if v is not None else 'N/A':>{col_width}s}"
        print(row)

    row = f"  {'Service Availability':<30s}"
    for r in results:
        v = r.metrics["service_availability"]
        row += f"{v:{col_width}.1%}"
    print(row)

    print(f"  {'─' * (30 + col_width * len(results))}")

    # Detection performance
    for label, key in [
        ("Precision", "precision"),
        ("Recall", "recall"),
        ("F1-Score", "f1_score"),
    ]:
        row = f"  {label:<30s}"
        for r in results:
            v = r.metrics["detection"][key]
            row += f"{v:{col_width}.3f}"
        print(row)

    # Improvement summary
    if len(results) >= 3:
        nm = results[0].metrics
        nat = results[1].metrics
        nep = results[2].metrics

        print(f"\n{_B}  Nephio Improvements:{_0}")

        def _show_imp(label, nm_v, nat_v, nep_v, lower_better=True):
            imp_vs_nm = _pct_improvement(nm_v, nep_v, lower_better)
            imp_vs_nat = _pct_improvement(nat_v, nep_v, lower_better)
            c1 = _G if imp_vs_nm > 0 else _R
            c2 = _G if imp_vs_nat > 0 else _R
            print(f"    {label:<26s}  vs No-Mitigation: {c1}{imp_vs_nm:+.1f}%{_0}  "
                  f"vs Native K8s: {c2}{imp_vs_nat:+.1f}%{_0}")

        _show_imp("Latency p50",
                  nm["service_during_attack"]["avg_latency_p50_ms"],
                  nat["service_during_attack"]["avg_latency_p50_ms"],
                  nep["service_during_attack"]["avg_latency_p50_ms"])
        _show_imp("Latency p99",
                  nm["service_during_attack"]["avg_latency_p99_ms"],
                  nat["service_during_attack"]["avg_latency_p99_ms"],
                  nep["service_during_attack"]["avg_latency_p99_ms"])
        _show_imp("Error Rate",
                  nm["service_during_attack"]["avg_error_rate"],
                  nat["service_during_attack"]["avg_error_rate"],
                  nep["service_during_attack"]["avg_error_rate"])
        _show_imp("Service Availability",
                  nm["service_availability"], nat["service_availability"],
                  nep["service_availability"], lower_better=False)

    print(f"\n{_B}{_C}{'=' * w}{_0}\n")


# ══════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare crossfire DDoS mitigation strategies (local simulation)"
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--duration", type=int, default=DEFAULT_DURATION,
                        help="Simulation duration in seconds (default: 1800)")
    parser.add_argument("--output-dir", type=str, default=None)
    args = parser.parse_args()

    warnings.filterwarnings("ignore", category=RuntimeWarning)
    warnings.filterwarnings("ignore", category=FutureWarning)
    logging.basicConfig(level=logging.WARNING)

    total_cycles = args.duration // INTERVAL_SECONDS
    rng = np.random.RandomState(args.seed)

    # ── Header ────────────────────────────────────────────────────
    w = 70
    print(f"\n{_B}{_C}{'=' * w}")
    print(f"  MITIGATION COMPARISON SIMULATION")
    print(f"  DDoS Detection + Mitigation Pipeline")
    print(f"{'=' * w}{_0}\n")
    print(f"  Duration:    {args.duration}s ({args.duration/60:.0f} min)")
    print(f"  Cycles:      {total_cycles} (every {INTERVAL_SECONDS}s)")
    print(f"  Seed:        {args.seed}")
    print(f"  Strategies:  No Mitigation, Native K8s, Nephio Automated")
    print()

    # ── Output directory ──────────────────────────────────────────
    if args.output_dir:
        out_dir = Path(args.output_dir)
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = Path("results") / f"mitigation_comparison_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"  Output:      {out_dir.resolve()}\n")

    # ── Schedule attacks (same for all scenarios) ─────────────────
    print(f"{_B}  Phase 1: Attack Scheduling{_0}")
    attacks = schedule_attacks(total_cycles, rng)
    print(f"  {_G}[OK]{_0}  Scheduled {len(attacks)} attacks\n")
    for a in attacks:
        name = a.attack_type.replace("_", " ").title()
        print(f"       #{a.attack_id+1:2d}  {name:.<30s}  "
              f"t={a.start_time_s/60:.1f}min  dur={a.duration_s/60:.1f}min  "
              f"(cycles {a.start_cycle}-{a.end_cycle-1})")
    print()

    # ── Train model ───────────────────────────────────────────────
    print(f"{_B}  Phase 2: Model Training{_0}")
    with tempfile.TemporaryDirectory(prefix="ddos_cmp_") as tmpdir:
        model_path = train_model(Path(tmpdir), args.seed)

        detector = AnomalyDetector(model_path, enable_xai=False)
        print(f"  {_G}[OK]{_0}  Detector ready\n")

        # ── Run all three scenarios ───────────────────────────────
        scenarios = [
            ("No Mitigation", NO_MITIGATION),
            ("Native K8s", NATIVE_K8S),
            ("Nephio Automated", NEPHIO_AUTOMATED),
        ]

        all_results: List[ScenarioResult] = []

        for i, (name, strategy) in enumerate(scenarios, 1):
            print(f"{_B}  Phase 3.{i}: Scenario — {name}{_0}")
            result = run_scenario(
                scenario_name=name,
                strategy=strategy,
                detector=detector,
                attacks=attacks,
                total_cycles=total_cycles,
                seed=args.seed,
            )
            metrics = compute_scenario_metrics(result)
            all_results.append(result)
            print(f"  {_G}[OK]{_0}  Completed: availability={metrics['service_availability']:.1%}  "
                  f"avg_eff={metrics['service_during_attack']['avg_mitigation_effectiveness']:.0%}\n")

    # ── Write outputs ─────────────────────────────────────────────
    print(f"{_B}  Phase 4: Writing Output Files{_0}")

    for result in all_results:
        scenario_dir = out_dir / result.name.lower().replace(" ", "_")
        write_scenario_outputs(result, scenario_dir)
        print(f"  {_G}[OK]{_0}  {scenario_dir}/")

    # Comparison table (Markdown)
    md_table = generate_comparison_table(all_results)
    md_path = out_dir / "comparison_table.md"
    md_path.write_text(md_table)
    print(f"  {_G}[OK]{_0}  {md_path}")

    # LaTeX tables
    latex = generate_latex_comparison(all_results)
    latex_path = out_dir / "latex_comparison.tex"
    latex_path.write_text(latex)
    print(f"  {_G}[OK]{_0}  {latex_path}")

    # Raw comparison JSON
    all_metrics = {r.name: r.metrics for r in all_results}
    json_path = out_dir / "comparison_data.json"
    with open(json_path, "w") as f:

        def _conv(obj):
            if isinstance(obj, (np.integer,)):
                return int(obj)
            if isinstance(obj, (np.floating,)):
                return float(obj)
            if isinstance(obj, np.ndarray):
                return obj.tolist()
            return obj

        json.dump(all_metrics, f, indent=2, default=_conv)
    print(f"  {_G}[OK]{_0}  {json_path}")

    # ── Print summary ─────────────────────────────────────────────
    print_comparison_summary(all_results)

    # ── Final verdict ─────────────────────────────────────────────
    nep = all_results[2].metrics
    nm = all_results[0].metrics
    nat = all_results[1].metrics

    nephio_wins = (
        nep["service_during_attack"]["avg_latency_p50_ms"] <
        nat["service_during_attack"]["avg_latency_p50_ms"]
        and
        nep["service_availability"] > nat["service_availability"]
    )

    if nephio_wins:
        print(f"  {_G}{_B}RESULT: Nephio automated mitigation outperforms both alternatives{_0}")
        print(f"  {_G}The intent-based approach provides faster detection, progressive")
        print(f"  mitigation with drift correction, and automated TTL-based revert.{_0}\n")
    else:
        print(f"  {_Y}{_B}RESULT: Review results — consider tuning mitigation parameters{_0}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
