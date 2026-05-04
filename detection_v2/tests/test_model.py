"""Tests for AnomalyDetector — load, detect, explain."""

import numpy as np
import pytest

from detection_v2.core.model import AnomalyDetector, classify_severity
from detection_v2.core.schema import DetectionResult, MetricSample, Severity
from detection_v2.core.errors import ModelLoadError
from detection_v2.core.features import FEATURE_NAMES
from detection_v2.tests.conftest import make_normal_samples, make_attack_samples


class TestClassifySeverity:
    def test_critical(self):
        assert classify_severity(-0.8) == Severity.CRITICAL

    def test_high(self):
        assert classify_severity(-0.6) == Severity.HIGH

    def test_medium(self):
        assert classify_severity(-0.4) == Severity.MEDIUM

    def test_low(self):
        assert classify_severity(0.0) == Severity.LOW

    def test_custom_thresholds(self):
        t = {Severity.CRITICAL: -0.9, Severity.HIGH: -0.8, Severity.MEDIUM: -0.5}
        assert classify_severity(-0.85, thresholds=t) == Severity.HIGH
        assert classify_severity(-0.6, thresholds=t) == Severity.MEDIUM


class TestAnomalyDetector:
    def test_load_nonexistent_raises(self, tmp_path):
        with pytest.raises(ModelLoadError, match="not found"):
            AnomalyDetector(tmp_path / "nonexistent.pkl")

    def test_load_and_detect_normal(self, trained_model_path, normal_sample):
        detector = AnomalyDetector(trained_model_path)
        result = detector.detect(normal_sample)

        assert isinstance(result, DetectionResult)
        assert result.feature_names == tuple(FEATURE_NAMES)
        assert result.scaled_features.shape == (len(FEATURE_NAMES),)
        assert result.detection_latency_ms > 0.0
        assert result.model_version != ""

    def test_detect_returns_immutable(self, trained_model_path, normal_sample):
        detector = AnomalyDetector(trained_model_path)
        result = detector.detect(normal_sample)

        with pytest.raises(AttributeError):
            result.is_anomaly = True  # type: ignore[misc]

    def test_normal_classified_normal(self, trained_model_path):
        """Most synthetic normal samples should be classified as normal."""
        detector = AnomalyDetector(trained_model_path)
        normals = make_normal_samples(50, rng_seed=123)

        results = [detector.detect(s) for s in normals]
        anomaly_count = sum(1 for r in results if r.is_anomaly)
        anomaly_rate = anomaly_count / len(results)

        # Allow up to 30% false positives (synthetic data is noisy)
        assert anomaly_rate < 0.30, (
            f"Too many false positives: {anomaly_rate:.0%}"
        )

    def test_attack_classified_anomaly(self, trained_model_path):
        """Synthetic attack samples should mostly be detected."""
        detector = AnomalyDetector(trained_model_path)
        attacks = make_attack_samples(30, rng_seed=77)

        results = [detector.detect(s) for s in attacks]
        detected = sum(1 for r in results if r.is_anomaly)
        recall = detected / len(results)

        assert recall > 0.5, f"Attack recall too low: {recall:.0%}"

    def test_detect_with_explanation(self, trained_model_path, attack_sample):
        detector = AnomalyDetector(trained_model_path)
        result = detector.detect(attack_sample, explain=True)

        if result.is_anomaly:
            assert result.explanation is not None
            assert len(result.explanation.contributions) == len(FEATURE_NAMES)

            # Verify explanation structure is internally consistent
            assert np.isfinite(result.explanation.base_value)
            assert np.isfinite(result.explanation.model_output)
            # All contribution values must be finite
            for c in result.explanation.contributions:
                assert np.isfinite(c.shap_value)
                assert np.isfinite(c.feature_value)
            # Contributions must be sorted by |shap_value| descending
            abs_vals = [abs(c.shap_value) for c in result.explanation.contributions]
            assert abs_vals == sorted(abs_vals, reverse=True)
        # If not anomaly, no explanation is generated (that's correct)

    def test_explanation_not_generated_for_normal(self, trained_model_path, normal_sample):
        detector = AnomalyDetector(trained_model_path)
        result = detector.detect(normal_sample, explain=True)

        if not result.is_anomaly:
            assert result.explanation is None

    def test_no_xai_mode(self, trained_model_path, attack_sample):
        detector = AnomalyDetector(trained_model_path, enable_xai=False)
        result = detector.detect(attack_sample, explain=True)
        # Even with explain=True, no explanation when xAI is disabled
        assert result.explanation is None

    def test_zero_sample_does_not_crash(self, trained_model_path, zero_sample):
        detector = AnomalyDetector(trained_model_path)
        result = detector.detect(zero_sample)
        assert isinstance(result, DetectionResult)

    def test_summary_string(self, trained_model_path, normal_sample):
        detector = AnomalyDetector(trained_model_path)
        result = detector.detect(normal_sample)
        s = result.summary()
        assert "score=" in s
        assert "severity=" in s
