"""
Continuous monitoring loop.

Replaces both ``continuous_monitor.py`` and ``cli_monitor.py`` from the
legacy system with a single, cleaner implementation that:

  1. Collects MetricSamples via an adapter.
  2. Runs inference via AnomalyDetector.
  3. Applies consecutive-detection logic before alerting.
  4. Handles MetricCollectionError and FeatureValidationError without
     crashing or silently mis-classifying.
  5. Writes structured JSONL alerts.
"""

from __future__ import annotations

import json
import logging
import signal
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Protocol

from ..core.schema import DetectionResult, MetricSample
from ..core.model import AnomalyDetector
from ..core.errors import MetricCollectionError, FeatureValidationError

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------
# Metric source protocol — any adapter satisfying this is accepted
# -----------------------------------------------------------------------

class MetricSource(Protocol):
    """Any object with a collect(window_seconds) → MetricSample method."""

    def collect(self, window_seconds: int = 30) -> MetricSample: ...


# -----------------------------------------------------------------------
# Monitor
# -----------------------------------------------------------------------

class ContinuousMonitor:
    """
    Poll a metric source, run inference, manage alert state.

    The monitor is deliberately framework-agnostic: it runs in a plain
    Python ``while`` loop.  For production, wrap it in a systemd service
    or a Kubernetes Deployment.
    """

    def __init__(
        self,
        detector: AnomalyDetector,
        source: MetricSource,
        *,
        interval_seconds: int = 15,
        window_seconds: int = 30,
        consecutive_threshold: int = 2,
        alert_cooldown_seconds: int = 60,
        alert_log_path: Optional[str | Path] = None,
        on_alert: Optional[Callable[[DetectionResult], None]] = None,
        min_request_rate: float = 1.0,
    ):
        self.detector = detector
        self.source = source
        self.interval = interval_seconds
        self.window = window_seconds
        self.consecutive_threshold = consecutive_threshold
        self.alert_cooldown = alert_cooldown_seconds
        self.alert_log_path = Path(alert_log_path) if alert_log_path else None
        self.on_alert = on_alert
        self.min_request_rate = min_request_rate

        # State
        self._running = False
        self._consecutive_anomalies = 0
        self._consecutive_normal = 0
        self._alert_active = False
        self._last_alert_time: Optional[float] = None
        self.stats = {
            "total_checks": 0,
            "anomalies": 0,
            "normal": 0,
            "alerts": 0,
            "collection_errors": 0,
            "validation_errors": 0,
        }

    # ------------------------------------------------------------------ #
    # Main loop
    # ------------------------------------------------------------------ #

    def run(self) -> None:
        """
        Start the monitoring loop.  Blocks until SIGINT/SIGTERM or
        ``stop()`` is called from another thread.
        """
        self._running = True
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        logger.info(
            "Monitor started — interval=%ds, window=%ds, threshold=%d",
            self.interval, self.window, self.consecutive_threshold,
        )

        try:
            while self._running:
                self._tick()
                if self._running:
                    time.sleep(self.interval)
        finally:
            self._print_summary()

    def stop(self) -> None:
        self._running = False

    # ------------------------------------------------------------------ #
    # Single detection cycle
    # ------------------------------------------------------------------ #

    def _tick(self) -> Optional[DetectionResult]:
        """Execute one collection → inference → alert cycle."""
        self.stats["total_checks"] += 1

        # 1. Collect
        try:
            sample = self.source.collect(window_seconds=self.window)
        except MetricCollectionError as exc:
            self.stats["collection_errors"] += 1
            logger.error("Metric collection failed (skipping cycle): %s", exc)
            return None

        # 2. Idle-traffic guard: skip anomaly detection when there is
        #    no meaningful traffic.  Zero/near-zero request rates are
        #    out-of-distribution for a model trained on live traffic and
        #    would produce false positives.  You cannot detect a DDoS
        #    attack when the system is simply idle.
        if sample.request_rate < self.min_request_rate:
            self.stats["normal"] += 1
            self._consecutive_anomalies = 0
            self._consecutive_normal += 1
            logger.debug(
                "Idle traffic (rate=%.2f < %.2f) — skipping detection",
                sample.request_rate, self.min_request_rate,
            )
            return None

        # 3. Detect
        try:
            result = self.detector.detect(sample, explain=True)
        except FeatureValidationError as exc:
            self.stats["validation_errors"] += 1
            logger.error("Feature validation failed (skipping cycle): %s", exc)
            return None

        # 4. Update state and alert
        if result.is_anomaly:
            self.stats["anomalies"] += 1
            self._consecutive_anomalies += 1
            self._consecutive_normal = 0

            if self._should_alert():
                self._fire_alert(result)
            else:
                logger.warning(
                    "Anomaly detected (score=%.3f, consecutive=%d/%d)",
                    result.anomaly_score,
                    self._consecutive_anomalies,
                    self.consecutive_threshold,
                )
        else:
            self.stats["normal"] += 1
            self._consecutive_normal += 1
            self._consecutive_anomalies = 0

            if self._alert_active and self._consecutive_normal >= self.consecutive_threshold:
                logger.info("Alert cleared — traffic returned to normal")
                self._alert_active = False

            logger.debug("Normal (score=%.3f)", result.anomaly_score)

        return result

    # ------------------------------------------------------------------ #
    # Alert logic
    # ------------------------------------------------------------------ #

    def _should_alert(self) -> bool:
        if self._consecutive_anomalies < self.consecutive_threshold:
            return False
        if self._alert_active:
            return False  # already alerting
        if self._last_alert_time is not None:
            if (time.time() - self._last_alert_time) < self.alert_cooldown:
                return False
        return True

    def _fire_alert(self, result: DetectionResult) -> None:
        self._alert_active = True
        self._last_alert_time = time.time()
        self.stats["alerts"] += 1

        logger.critical(
            "ALERT — anomaly score=%.3f, severity=%s, %d consecutive",
            result.anomaly_score,
            result.severity.value,
            self._consecutive_anomalies,
        )

        # Structured log
        if self.alert_log_path:
            self._write_alert(result)

        # Callback
        if self.on_alert:
            try:
                self.on_alert(result)
            except Exception:
                logger.exception("on_alert callback failed")

    def _write_alert(self, result: DetectionResult) -> None:
        self.alert_log_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": datetime.fromtimestamp(result.timestamp).isoformat(),
            "anomaly_score": result.anomaly_score,
            "severity": result.severity.value,
            "model_version": result.model_version,
            "detection_latency_ms": result.detection_latency_ms,
        }
        if result.explanation:
            record["top_contributions"] = [
                {
                    "feature": c.feature_name,
                    "shap_value": c.shap_value,
                    "feature_value": c.feature_value,
                }
                for c in result.explanation.contributions[:5]
            ]
            record["shap_base_value"] = result.explanation.base_value
        with open(self.alert_log_path, "a") as f:
            f.write(json.dumps(record) + "\n")

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def _handle_signal(self, signum, frame):
        logger.info("Received signal %d — stopping", signum)
        self._running = False

    def _print_summary(self) -> None:
        s = self.stats
        total = s["total_checks"] or 1
        logger.info(
            "Monitor stopped — %d checks: %d normal (%.0f%%), "
            "%d anomalies (%.0f%%), %d alerts, "
            "%d collection errors, %d validation errors",
            s["total_checks"],
            s["normal"], s["normal"] / total * 100,
            s["anomalies"], s["anomalies"] / total * 100,
            s["alerts"],
            s["collection_errors"],
            s["validation_errors"],
        )
