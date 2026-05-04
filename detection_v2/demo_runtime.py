#!/usr/bin/env python3
"""
Runtime demonstration of the detection_v2 monitoring pipeline.

Scenarios:
  1. IDLE      — No traffic (zero metrics).  Monitor should report NORMAL.
  2. NORMAL    — Simulated healthy traffic.   Monitor should report NORMAL.
  3. ATTACK    — Simulated volumetric DDoS.   Monitor should detect anomalies and fire alerts.
  4. CROSSFIRE — Simulated indirect DDoS.     Monitor should detect the crossfire pattern.
  5. RECOVERY  — Attack stops, normal resumes.  Alert should clear.

Usage:
    python -m detection_v2.demo_runtime
"""

from __future__ import annotations

import logging
import sys
import tempfile
import time
from pathlib import Path
from typing import List, Optional

import numpy as np

from detection_v2.core.schema import MetricSample, DetectionResult, Severity
from detection_v2.core.model import AnomalyDetector
from detection_v2.training.trainer import Trainer
from detection_v2.monitor.continuous import ContinuousMonitor

# ──────────────────────────────────────────────────────────────────────────
# Colours for terminal output
# ──────────────────────────────────────────────────────────────────────────
_GREEN = "\033[92m"
_RED = "\033[91m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def _header(title: str) -> None:
    width = 70
    print(f"\n{_BOLD}{_CYAN}{'=' * width}")
    print(f"  {title}")
    print(f"{'=' * width}{_RESET}\n")


def _ok(msg: str) -> None:
    print(f"  {_GREEN}[OK]{_RESET}  {msg}")


def _warn(msg: str) -> None:
    print(f"  {_YELLOW}[!!]{_RESET}  {msg}")


def _fail(msg: str) -> None:
    print(f"  {_RED}[FAIL]{_RESET}  {msg}")


def _info(msg: str) -> None:
    print(f"  {_CYAN}[..]{_RESET}  {msg}")


# ──────────────────────────────────────────────────────────────────────────
# Simulated traffic source — replays pre-built sample sequences
# ──────────────────────────────────────────────────────────────────────────

class SimulatedSource:
    """
    Metric source that replays a list of MetricSamples.
    Satisfies the MetricSource protocol used by ContinuousMonitor.
    """

    def __init__(self, samples: List[MetricSample]):
        self._samples = samples
        self._index = 0

    def collect(self, window_seconds: int = 30) -> MetricSample:
        if self._index >= len(self._samples):
            self._index = 0  # loop
        sample = self._samples[self._index]
        self._index += 1
        return sample

    def reset(self, samples: Optional[List[MetricSample]] = None) -> None:
        self._index = 0
        if samples is not None:
            self._samples = samples


# ──────────────────────────────────────────────────────────────────────────
# Sample generators
# ──────────────────────────────────────────────────────────────────────────

def _make_idle_samples(n: int = 10) -> List[MetricSample]:
    """Zero traffic — system is idle."""
    return [MetricSample.zero() for _ in range(n)]


def _make_normal_samples(n: int = 20, seed: int = 42) -> List[MetricSample]:
    """Healthy traffic with natural variance."""
    rng = np.random.RandomState(seed)
    out = []
    for i in range(n):
        out.append(MetricSample(
            timestamp=time.time() + i,
            request_rate=max(0.0, 50.0 + rng.normal(0, 5)),
            request_rate_variance=max(0.0, 4.0 + rng.normal(0, 1)),
            latency_p50_ms=max(0.0, 12.0 + rng.normal(0, 2)),
            latency_p95_ms=max(0.0, 45.0 + rng.normal(0, 5)),
            latency_p99_ms=max(0.0, 120.0 + rng.normal(0, 10)),
            error_rate=max(0.0, 0.5 + rng.normal(0, 0.2)),
            total_request_rate=max(0.001, 50.0 + rng.normal(0, 5)),
            byte_rate_in=max(0.0, 25000.0 + rng.normal(0, 2000)),
            byte_rate_out=max(0.0, 125000.0 + rng.normal(0, 10000)),
            avg_request_size_bytes=max(0.0, 500.0 + rng.normal(0, 50)),
            avg_response_size_bytes=max(0.0, 2500.0 + rng.normal(0, 200)),
            connection_open_rate=max(0.0, 8.0 + rng.normal(0, 1)),
            connection_close_rate=max(0.0, 7.5 + rng.normal(0, 1)),
        ))
    return out


def _make_attack_samples(n: int = 15, seed: int = 99) -> List[MetricSample]:
    """Volumetric DDoS — massive rate, high variance, high error rate."""
    rng = np.random.RandomState(seed)
    out = []
    for i in range(n):
        out.append(MetricSample(
            timestamp=time.time() + i,
            request_rate=max(0.0, 5000.0 + rng.normal(0, 500)),
            request_rate_variance=max(0.0, 2000.0 + rng.normal(0, 300)),
            latency_p50_ms=max(0.0, 500.0 + rng.normal(0, 100)),
            latency_p95_ms=max(0.0, 3000.0 + rng.normal(0, 500)),
            latency_p99_ms=max(0.0, 8000.0 + rng.normal(0, 1000)),
            error_rate=max(0.0, 400.0 + rng.normal(0, 50)),
            total_request_rate=max(0.001, 5000.0 + rng.normal(0, 500)),
            byte_rate_in=max(0.0, 5000000.0 + rng.normal(0, 500000)),
            byte_rate_out=max(0.0, 500000.0 + rng.normal(0, 50000)),
            avg_request_size_bytes=max(0.0, 100.0 + rng.normal(0, 20)),
            avg_response_size_bytes=max(0.0, 50.0 + rng.normal(0, 10)),
            connection_open_rate=max(0.0, 2000.0 + rng.normal(0, 200)),
            connection_close_rate=max(0.0, 200.0 + rng.normal(0, 50)),
        ))
    return out


def _make_crossfire_samples(n: int = 10) -> List[MetricSample]:
    """
    Indirect (Crossfire) DDoS — moderate request rate but:
      - Very high connection churn (open >> close)
      - Asymmetric byte rates (massive inbound, tiny outbound)
      - Extreme tail latency
      - Elevated errors from link congestion
    """
    rng = np.random.RandomState(77)
    out = []
    for i in range(n):
        out.append(MetricSample(
            timestamp=time.time() + i,
            request_rate=max(0.0, 80.0 + rng.normal(0, 10)),
            request_rate_variance=max(0.0, 45.0 + rng.normal(0, 8)),
            latency_p50_ms=max(0.0, 200.0 + rng.normal(0, 30)),
            latency_p95_ms=max(0.0, 1500.0 + rng.normal(0, 200)),
            latency_p99_ms=max(0.0, 5000.0 + rng.normal(0, 500)),
            error_rate=max(0.0, 15.0 + rng.normal(0, 3)),
            total_request_rate=max(0.001, 80.0 + rng.normal(0, 10)),
            byte_rate_in=max(0.0, 3000000.0 + rng.normal(0, 300000)),
            byte_rate_out=max(0.0, 60000.0 + rng.normal(0, 8000)),
            avg_request_size_bytes=max(0.0, 1200.0 + rng.normal(0, 100)),
            avg_response_size_bytes=max(0.0, 100.0 + rng.normal(0, 20)),
            connection_open_rate=max(0.0, 500.0 + rng.normal(0, 50)),
            connection_close_rate=max(0.0, 50.0 + rng.normal(0, 10)),
        ))
    return out


# ──────────────────────────────────────────────────────────────────────────
# Training helper
# ──────────────────────────────────────────────────────────────────────────

def train_model(model_dir: Path) -> Path:
    """Train on synthetic normal traffic, save artifact, return path."""
    _info("Generating 500 normal + 100 attack training samples ...")
    normal = _make_normal_samples(500, seed=1)
    attack = _make_attack_samples(100, seed=2)

    _info("Training Isolation Forest (200 estimators) ...")
    trainer = Trainer(
        n_estimators=200,
        max_samples=256,
        shap_background_size=100,
        contamination=0.05,
    )
    results = trainer.fit(normal, attack_samples=attack)

    model_path = model_dir / "demo_model.pkl"
    trainer.save(model_path)

    _ok(f"Model saved → {model_path}")
    _ok(f"Training accuracy: {results['accuracy']:.1%}")
    _ok(f"Attack recall:     {results['attack_recall']:.1%}")
    _ok(f"Normal FP rate:    {results.get('normal_false_positive_rate', 0):.1%}")
    return model_path


# ──────────────────────────────────────────────────────────────────────────
# Scenario runner — drives ContinuousMonitor._tick() in a loop
# ──────────────────────────────────────────────────────────────────────────

def run_scenario(
    name: str,
    detector: AnomalyDetector,
    samples: List[MetricSample],
    alert_log_path: Path,
    *,
    n_ticks: int = 10,
    consecutive_threshold: int = 2,
    expect_alerts: bool = False,
) -> bool:
    """
    Run n_ticks of the monitor with the given samples.
    Returns True if behaviour matches expectations.
    """
    _header(f"Scenario: {name}")
    _info(f"Running {n_ticks} detection cycles (consecutive_threshold={consecutive_threshold})...")
    print()

    alerts_fired: List[DetectionResult] = []

    def on_alert(result: DetectionResult) -> None:
        alerts_fired.append(result)

    source = SimulatedSource(samples)
    monitor = ContinuousMonitor(
        detector=detector,
        source=source,
        interval_seconds=0,  # no delay in demo
        window_seconds=30,
        consecutive_threshold=consecutive_threshold,
        alert_cooldown_seconds=0,  # allow rapid re-alerting for demo
        alert_log_path=alert_log_path,
        on_alert=on_alert,
    )

    results: List[DetectionResult] = []
    for tick in range(n_ticks):
        r = monitor._tick()
        if r is not None:
            results.append(r)
            status = "ANOMALY" if r.is_anomaly else "NORMAL "
            sev = r.severity.value.ljust(8)
            colour = _RED if r.is_anomaly else _GREEN
            alert_flag = f"  {_RED}{_BOLD}<<< ALERT >>>{_RESET}" if len(alerts_fired) > len(results) - 1 and r.is_anomaly and any(a is r for a in alerts_fired) else ""
            # Check if this tick triggered an alert
            was_alert = len(alerts_fired) > 0 and alerts_fired[-1] is r
            alert_str = f"  {_RED}{_BOLD}<<< ALERT FIRED >>>{_RESET}" if was_alert and tick >= 1 else ""

            print(f"    tick {tick+1:2d}  │  {colour}{status}{_RESET}  │  "
                  f"score={r.anomaly_score:+.4f}  │  severity={sev}"
                  f"{alert_str}")

            # Show SHAP explanation for alerted anomalies
            if was_alert and r.explanation:
                print(f"             │  {_YELLOW}Top contributors:{_RESET}")
                for c in r.explanation.contributions[:3]:
                    print(f"             │    {c.feature_name}: "
                          f"shap={c.shap_value:+.4f}, value={c.feature_value:.1f}")

    print()

    # Summarise
    n_anomalies = sum(1 for r in results if r.is_anomaly)
    n_normal = sum(1 for r in results if not r.is_anomaly)
    n_alerts = len(alerts_fired)

    _info(f"Results: {n_normal} normal, {n_anomalies} anomalies, {n_alerts} alerts fired")

    passed = True
    if expect_alerts:
        if n_alerts > 0:
            _ok(f"Alerts correctly fired ({n_alerts} alert(s))")
        else:
            _fail("Expected alerts but none were fired!")
            passed = False
        if n_anomalies == 0:
            _fail("Expected anomaly detections but got none!")
            passed = False
    else:
        if n_alerts == 0:
            _ok("No alerts fired (correct — no attack traffic)")
        else:
            _fail(f"Expected no alerts but {n_alerts} were fired!")
            passed = False

    return passed


# ──────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────

def main() -> int:
    # Suppress noisy warnings from sklearn/shap during demo
    import warnings
    warnings.filterwarnings("ignore", category=RuntimeWarning)
    warnings.filterwarnings("ignore", category=FutureWarning)

    # Configure logging — show CRITICAL alerts only (the monitor logs them)
    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)-8s %(message)s",
    )

    _header("detection_v2  —  Runtime Demonstration")
    print("  This demo trains a model on synthetic data, then runs the")
    print("  continuous monitor through five scenarios to verify correct")
    print("  detection behaviour.\n")

    with tempfile.TemporaryDirectory(prefix="ddos_demo_") as tmpdir:
        tmpdir = Path(tmpdir)

        # ── Phase 1: Train ────────────────────────────────────────────
        _header("Phase 1: Model Training")
        model_path = train_model(tmpdir)

        # ── Load detector ─────────────────────────────────────────────
        _info("Loading AnomalyDetector with SHAP explainer ...")
        detector = AnomalyDetector(model_path, enable_xai=True)
        _ok(f"Detector ready (model version: {detector.model_version})")

        alert_log = tmpdir / "alerts.jsonl"
        all_passed = True

        # ── Scenario 1: IDLE (no traffic) ─────────────────────────────
        ok = run_scenario(
            "IDLE — No Traffic (zero metrics)",
            detector,
            _make_idle_samples(10),
            alert_log,
            n_ticks=8,
            expect_alerts=False,
        )
        all_passed &= ok

        # ── Scenario 2: NORMAL traffic ────────────────────────────────
        ok = run_scenario(
            "NORMAL — Healthy Traffic",
            detector,
            _make_normal_samples(20, seed=200),
            alert_log,
            n_ticks=10,
            expect_alerts=False,
        )
        all_passed &= ok

        # ── Scenario 3: VOLUMETRIC ATTACK ─────────────────────────────
        ok = run_scenario(
            "ATTACK — Volumetric DDoS (high rate, high variance)",
            detector,
            _make_attack_samples(15, seed=300),
            alert_log,
            n_ticks=10,
            expect_alerts=True,
        )
        all_passed &= ok

        # ── Scenario 4: CROSSFIRE (indirect DDoS) ─────────────────────
        ok = run_scenario(
            "CROSSFIRE — Indirect DDoS (asymmetric bytes, connection churn)",
            detector,
            _make_crossfire_samples(10),
            alert_log,
            n_ticks=8,
            expect_alerts=True,
        )
        all_passed &= ok

        # ── Scenario 5: RECOVERY (attack → normal) ────────────────────
        _header("Scenario: RECOVERY — Attack stops, normal traffic resumes")
        _info("Feeding 4 attack ticks then 6 normal ticks ...")
        print()

        recovery_alerts: List[DetectionResult] = []
        recovery_source = SimulatedSource(
            _make_attack_samples(4, seed=400) + _make_normal_samples(6, seed=401)
        )
        recovery_monitor = ContinuousMonitor(
            detector=detector,
            source=recovery_source,
            interval_seconds=0,
            window_seconds=30,
            consecutive_threshold=2,
            alert_cooldown_seconds=0,
            alert_log_path=alert_log,
            on_alert=lambda r: recovery_alerts.append(r),
        )

        phase_results = []
        for tick in range(10):
            r = recovery_monitor._tick()
            if r:
                phase_results.append(r)
                phase = "ATTACK " if tick < 4 else "NORMAL "
                status = "ANOMALY" if r.is_anomaly else "NORMAL "
                colour = _RED if r.is_anomaly else _GREEN
                print(f"    tick {tick+1:2d}  │  phase={phase} │  "
                      f"{colour}{status}{_RESET}  │  score={r.anomaly_score:+.4f}")

        print()
        atk_phase_anomalies = sum(1 for r in phase_results[:4] if r.is_anomaly)
        norm_phase_anomalies = sum(1 for r in phase_results[4:] if r.is_anomaly)
        _info(f"Attack phase: {atk_phase_anomalies}/4 detected as anomaly")
        _info(f"Normal phase: {norm_phase_anomalies}/6 detected as anomaly")

        if atk_phase_anomalies > 0 and norm_phase_anomalies < len(phase_results[4:]):
            _ok("System correctly detected attack and recovered to normal")
        else:
            _fail("Recovery behaviour not as expected")
            all_passed = False

        # ── Alert log ──────────────────────────────────────────────────
        _header("Alert Log (JSONL)")
        if alert_log.exists():
            content = alert_log.read_text()
            lines = content.strip().split("\n")
            _info(f"{len(lines)} alert(s) logged to {alert_log}")
            for i, line in enumerate(lines[:5], 1):
                import json
                record = json.loads(line)
                sev = record.get("severity", "?")
                score = record.get("anomaly_score", 0)
                print(f"    {i}. severity={sev}, score={score:.4f}")
                if "top_contributions" in record:
                    for c in record["top_contributions"][:2]:
                        print(f"       └─ {c['feature']}: shap={c['shap_value']:.4f}")
            if len(lines) > 5:
                print(f"    ... and {len(lines) - 5} more")
        else:
            _warn("No alert log file created")

        # ── Final verdict ──────────────────────────────────────────────
        _header("Final Verdict")
        if all_passed:
            print(f"  {_GREEN}{_BOLD}ALL SCENARIOS PASSED{_RESET}")
            print(f"  {_GREEN}The monitoring system correctly:{_RESET}")
            print(f"    - Reports NORMAL when there is no traffic (idle)")
            print(f"    - Reports NORMAL when traffic is healthy")
            print(f"    - Detects and alerts on volumetric DDoS attacks")
            print(f"    - Detects and alerts on crossfire (indirect) DDoS")
            print(f"    - Recovers to normal after attack traffic stops")
            return 0
        else:
            print(f"  {_RED}{_BOLD}SOME SCENARIOS FAILED{_RESET}")
            print(f"  Review the output above for details.")
            return 1


if __name__ == "__main__":
    sys.exit(main())
