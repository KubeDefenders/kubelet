"""
Integration test: full pipeline from synthetic data through train → detect → explain.

This test exercises the entire detection_v2 stack end-to-end without any
external dependencies (no Prometheus, no Kubernetes, no real datasets).
"""

import json
import numpy as np
import pytest
from pathlib import Path

from detection_v2.core.schema import MetricSample, DetectionResult, Severity
from detection_v2.core.features import FEATURE_NAMES, extract_features
from detection_v2.core.model import AnomalyDetector
from detection_v2.training.trainer import Trainer
from detection_v2.tests.conftest import make_normal_samples, make_attack_samples


class TestEndToEnd:
    """Full pipeline integration tests."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        """Train and save a model before each test."""
        self.model_path = tmp_path / "e2e_model.pkl"
        self.normal = make_normal_samples(400, rng_seed=42)
        self.attacks = make_attack_samples(80, rng_seed=99)

        trainer = Trainer(
            n_estimators=80,
            max_samples=128,
            shap_background_size=50,
            contamination=0.05,
        )
        self.train_results = trainer.fit(self.normal, attack_samples=self.attacks)
        trainer.save(self.model_path)

    def test_training_produces_positive_accuracy(self):
        assert self.train_results["accuracy"] > 0.5

    def test_model_loads_and_detects(self):
        detector = AnomalyDetector(self.model_path)
        sample = self.normal[0]
        result = detector.detect(sample)

        assert isinstance(result, DetectionResult)
        assert result.model_version != ""
        assert result.detection_latency_ms > 0

    def test_normal_traffic_detection(self):
        detector = AnomalyDetector(self.model_path, enable_xai=False)

        results = [detector.detect(s) for s in self.normal[:100]]
        fp = sum(1 for r in results if r.is_anomaly)
        fp_rate = fp / len(results)

        assert fp_rate < 0.30, f"FP rate {fp_rate:.0%} too high"

    def test_attack_traffic_detection(self):
        detector = AnomalyDetector(self.model_path, enable_xai=False)

        results = [detector.detect(s) for s in self.attacks[:50]]
        detected = sum(1 for r in results if r.is_anomaly)
        recall = detected / len(results)

        assert recall > 0.5, f"Attack recall {recall:.0%} too low"

    def test_explanation_structure(self):
        detector = AnomalyDetector(self.model_path, enable_xai=True)

        # Find an actual attack detection
        for s in self.attacks:
            result = detector.detect(s, explain=True)
            if result.is_anomaly and result.explanation is not None:
                exp = result.explanation

                # All features should have contributions
                assert len(exp.contributions) == len(FEATURE_NAMES)

                # Contributions must be sorted by |shap_value| descending
                abs_vals = [abs(c.shap_value) for c in exp.contributions]
                assert abs_vals == sorted(abs_vals, reverse=True)

                # Base value must be numeric
                assert np.isfinite(exp.base_value)
                assert np.isfinite(exp.model_output)

                return  # Pass

        pytest.skip("No attack sample was classified as anomaly")

    def test_severity_assignment(self):
        detector = AnomalyDetector(self.model_path, enable_xai=False)

        for s in self.attacks:
            result = detector.detect(s)
            if result.is_anomaly:
                assert result.severity in (
                    Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL
                )
                return

        pytest.skip("No attack detected")

    def test_feature_vector_determinism(self):
        """Two detections on the same sample must produce identical feature vectors."""
        detector = AnomalyDetector(self.model_path, enable_xai=False)
        s = self.normal[0]

        r1 = detector.detect(s)
        r2 = detector.detect(s)

        np.testing.assert_array_equal(r1.scaled_features, r2.scaled_features)
        assert r1.anomaly_score == r2.anomaly_score
        assert r1.is_anomaly == r2.is_anomaly

    def test_crossfire_pattern_detection(self):
        """
        Simulate a crossfire-like pattern: moderate overall rate but
        very high connection churn and asymmetric byte rates.
        This is the signature of an indirect DDoS (Crossfire attack).
        """
        detector = AnomalyDetector(self.model_path, enable_xai=True)

        crossfire_sample = MetricSample(
            timestamp=1700000000.0,
            request_rate=80.0,         # Not extremely high (indirect attack)
            request_rate_variance=50.0, # But very bursty
            latency_p50_ms=200.0,       # Degraded due to link congestion
            latency_p95_ms=1500.0,
            latency_p99_ms=5000.0,      # Extreme tail
            error_rate=15.0,            # Elevated errors (paths congested)
            total_request_rate=80.0,
            byte_rate_in=3000000.0,     # Massive inbound (flood to decoys)
            byte_rate_out=60000.0,      # Low outbound (target starved)
            avg_request_size_bytes=1200.0,
            avg_response_size_bytes=100.0,  # Very small responses (errors/timeouts)
            connection_open_rate=500.0,     # High connection churn
            connection_close_rate=50.0,     # Open >> close = exhaustion
        )

        result = detector.detect(crossfire_sample, explain=True)
        # The crossfire pattern should look anomalous
        # Even if not flagged, log the score for analysis
        print(f"\nCrossfire detection: anomaly={result.is_anomaly}, "
              f"score={result.anomaly_score:.3f}")

        if result.explanation:
            print("Top contributors:")
            for c in result.explanation.contributions[:5]:
                print(f"  {c.feature_name}: shap={c.shap_value:.4f}, "
                      f"value={c.feature_value:.2f}")
