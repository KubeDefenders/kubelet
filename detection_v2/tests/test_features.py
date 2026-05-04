"""Tests for feature extraction — determinism, correctness, validation."""

import numpy as np
import pytest

from detection_v2.core.schema import MetricSample
from detection_v2.core.features import (
    FEATURE_NAMES,
    extract_features,
    validate_feature_vector,
)
from detection_v2.core.errors import FeatureValidationError


class TestExtractFeatures:
    def test_output_shape(self, normal_sample):
        v = extract_features(normal_sample)
        assert v.shape == (len(FEATURE_NAMES),)
        assert v.dtype == np.float64

    def test_determinism(self, normal_sample):
        """Same input must always produce the same output."""
        v1 = extract_features(normal_sample)
        v2 = extract_features(normal_sample)
        np.testing.assert_array_equal(v1, v2)

    def test_no_nan_inf(self, normal_sample):
        v = extract_features(normal_sample)
        assert not np.any(np.isnan(v)), "Feature vector contains NaN"
        assert not np.any(np.isinf(v)), "Feature vector contains Inf"

    def test_zero_sample_no_nan(self, zero_sample):
        """A fully-zeroed input must not produce NaN (division by zero guard)."""
        v = extract_features(zero_sample)
        assert not np.any(np.isnan(v))
        assert not np.any(np.isinf(v))

    def test_latency_spread(self, normal_sample):
        v = extract_features(normal_sample)
        idx_spread = FEATURE_NAMES.index("latency_spread_ms")
        idx_p99 = FEATURE_NAMES.index("latency_p99_ms")
        idx_p50 = FEATURE_NAMES.index("latency_p50_ms")
        assert v[idx_spread] == pytest.approx(v[idx_p99] - v[idx_p50])

    def test_error_ratio(self, normal_sample):
        v = extract_features(normal_sample)
        idx = FEATURE_NAMES.index("error_ratio")
        expected = normal_sample.error_rate / max(normal_sample.total_request_rate, 1e-9)
        assert v[idx] == pytest.approx(expected)

    def test_burstiness(self, normal_sample):
        v = extract_features(normal_sample)
        idx = FEATURE_NAMES.index("burstiness")
        expected = normal_sample.request_rate_variance / (
            normal_sample.request_rate + 1e-9
        )
        assert v[idx] == pytest.approx(expected)

    def test_net_connection_rate(self, normal_sample):
        v = extract_features(normal_sample)
        idx = FEATURE_NAMES.index("net_connection_rate")
        expected = normal_sample.connection_open_rate - normal_sample.connection_close_rate
        assert v[idx] == pytest.approx(expected)

    def test_attack_sample_larger_features(self, normal_sample, attack_sample):
        """Attack traffic should produce larger request_rate and burstiness."""
        v_norm = extract_features(normal_sample)
        v_atk = extract_features(attack_sample)
        idx_rate = FEATURE_NAMES.index("request_rate")
        idx_burst = FEATURE_NAMES.index("burstiness")
        assert v_atk[idx_rate] > v_norm[idx_rate]
        assert v_atk[idx_burst] > v_norm[idx_burst]

    def test_feature_count_matches_names(self):
        assert len(FEATURE_NAMES) == 16


class TestValidateFeatureVector:
    def test_valid_passes(self, normal_sample):
        v = extract_features(normal_sample)
        validate_feature_vector(v)  # should not raise

    def test_wrong_shape_raises(self):
        with pytest.raises(FeatureValidationError, match="shape"):
            validate_feature_vector(np.array([1.0, 2.0]))

    def test_nan_raises(self):
        v = np.zeros(len(FEATURE_NAMES))
        v[3] = float("nan")
        with pytest.raises(FeatureValidationError, match="NaN"):
            validate_feature_vector(v)

    def test_inf_raises(self):
        v = np.zeros(len(FEATURE_NAMES))
        v[0] = float("inf")
        with pytest.raises(FeatureValidationError, match="Inf"):
            validate_feature_vector(v)

    def test_extreme_values_warn(self):
        v = np.ones(len(FEATURE_NAMES)) * 20.0
        with pytest.warns(RuntimeWarning, match="exceed"):
            validate_feature_vector(v)
