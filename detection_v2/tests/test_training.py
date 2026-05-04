"""Tests for training pipeline — fit, save, load round-trip."""

import json
from pathlib import Path

import joblib
import numpy as np
import pytest

from detection_v2.core.features import FEATURE_NAMES
from detection_v2.training.trainer import Trainer
from detection_v2.tests.conftest import make_normal_samples, make_attack_samples


class TestTrainer:
    def test_fit_returns_results(self):
        normal = make_normal_samples(200)
        attack = make_attack_samples(30)

        trainer = Trainer(n_estimators=20, max_samples=32, shap_background_size=20)
        results = trainer.fit(normal, attack_samples=attack)

        assert "accuracy" in results
        assert "attack_recall" in results
        assert results["accuracy"] > 0.0

    def test_fit_without_attack_data(self):
        normal = make_normal_samples(100)

        trainer = Trainer(n_estimators=20, max_samples=32, shap_background_size=20)
        results = trainer.fit(normal)

        assert "normal_val_false_positive_rate" in results

    def test_save_creates_file(self, tmp_path):
        normal = make_normal_samples(100)
        trainer = Trainer(n_estimators=20, max_samples=32, shap_background_size=20)
        trainer.fit(normal)

        model_path = tmp_path / "model.pkl"
        returned_path = trainer.save(model_path)

        assert returned_path.exists()
        assert returned_path == model_path

    def test_artifact_contains_required_keys(self, tmp_path):
        normal = make_normal_samples(100)
        trainer = Trainer(n_estimators=20, max_samples=32, shap_background_size=20)
        trainer.fit(normal)

        model_path = tmp_path / "model.pkl"
        trainer.save(model_path)

        artifact = joblib.load(model_path)
        for key in ("model", "scaler", "feature_names", "shap_background"):
            assert key in artifact, f"Missing key: {key}"

    def test_feature_names_match(self, tmp_path):
        normal = make_normal_samples(100)
        trainer = Trainer(n_estimators=20, max_samples=32, shap_background_size=20)
        trainer.fit(normal)

        model_path = tmp_path / "model.pkl"
        trainer.save(model_path)

        artifact = joblib.load(model_path)
        assert artifact["feature_names"] == list(FEATURE_NAMES)

    def test_shap_background_shape(self, tmp_path):
        bg_size = 25
        normal = make_normal_samples(100)
        trainer = Trainer(
            n_estimators=20, max_samples=32, shap_background_size=bg_size
        )
        trainer.fit(normal)

        model_path = tmp_path / "model.pkl"
        trainer.save(model_path)

        artifact = joblib.load(model_path)
        bg = artifact["shap_background"]
        assert bg.shape == (bg_size, len(FEATURE_NAMES))
        assert not np.any(np.isnan(bg))

    def test_save_before_fit_raises(self, tmp_path):
        trainer = Trainer()
        with pytest.raises(RuntimeError, match="fit"):
            trainer.save(tmp_path / "bad.pkl")

    def test_attack_recall_above_threshold(self):
        """The model should detect most synthetic attacks."""
        normal = make_normal_samples(500)
        attack = make_attack_samples(100)

        trainer = Trainer(n_estimators=50, max_samples=64, shap_background_size=50)
        results = trainer.fit(normal, attack_samples=attack)

        # With well-separated synthetic data, recall should be high
        assert results["attack_recall"] > 0.5, (
            f"Attack recall too low: {results['attack_recall']:.3f}"
        )
