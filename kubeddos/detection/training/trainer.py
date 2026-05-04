"""
Model training pipeline.

Accepts MetricSample objects (from any adapter), extracts the canonical
feature vector, fits an Isolation Forest, and serialises the artifact
in the format expected by ``AnomalyDetector``.

Key improvements over the legacy trainer:
  - One model (IF), one feature set — no ensemble ambiguity.
  - Real SHAP background data saved with the artifact.
  - Feature names are stored and validated on load.
  - Evaluation uses both normal and attack data for TPR/FPR reporting.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import RobustScaler

from ..core.schema import MetricSample
from ..core.features import FEATURE_NAMES, extract_features

logger = logging.getLogger(__name__)


class Trainer:
    """
    Trains an Isolation Forest anomaly detector on normal-traffic
    MetricSample objects.

    Usage::

        from detection_v2.adapters import CICDDoS2019Adapter
        adapter = CICDDoS2019Adapter("/data/cicddos2019")
        normal, attack = adapter.load()

        trainer = Trainer()
        trainer.fit(normal, attack_samples=attack)
        trainer.save("models/detector_v2.pkl")
    """

    def __init__(
        self,
        *,
        contamination: float = 0.05,
        n_estimators: int = 200,
        max_samples: int = 256,
        random_state: int = 42,
        validation_split: float = 0.2,
        shap_background_size: int = 200,
    ):
        self.contamination = contamination
        self.n_estimators = n_estimators
        self.max_samples = max_samples
        self.random_state = random_state
        self.validation_split = validation_split
        self.shap_background_size = shap_background_size

        self.model: Optional[IsolationForest] = None
        self.scaler: Optional[RobustScaler] = None
        self.shap_background: Optional[np.ndarray] = None
        self._eval_results: Optional[Dict] = None

    # ------------------------------------------------------------------ #
    # Feature matrix construction
    # ------------------------------------------------------------------ #

    @staticmethod
    def _samples_to_matrix(samples: List[MetricSample]) -> np.ndarray:
        """Convert a list of MetricSample to a feature matrix (n, 16)."""
        rows = []
        for s in samples:
            try:
                rows.append(extract_features(s))
            except Exception:
                # Skip samples that fail validation (inf in source data)
                continue
        if not rows:
            raise ValueError("No valid feature vectors extracted from input data")
        X = np.vstack(rows)
        # Replace any remaining pathological values
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
        return X

    # ------------------------------------------------------------------ #
    # Training
    # ------------------------------------------------------------------ #

    def fit(
        self,
        normal_samples: List[MetricSample],
        *,
        attack_samples: Optional[List[MetricSample]] = None,
    ) -> Dict:
        """
        Fit the model on normal traffic, optionally evaluate on attack data.

        Returns:
            Evaluation results dict (empty if no attack_samples provided).
        """
        logger.info(
            "Building feature matrix from %d normal samples", len(normal_samples)
        )
        X_normal = self._samples_to_matrix(normal_samples)
        logger.info("Feature matrix shape: %s", X_normal.shape)

        # --- Scale ---
        self.scaler = RobustScaler()
        X_scaled = self.scaler.fit_transform(X_normal)

        # --- Clip extreme outliers (99.5th percentile) ---
        clip = np.percentile(np.abs(X_scaled), 99.5)
        X_scaled = np.clip(X_scaled, -clip, clip)

        # --- Train/val split ---
        X_train, X_val = train_test_split(
            X_scaled,
            test_size=self.validation_split,
            random_state=self.random_state,
        )
        logger.info("Training on %d samples, validating on %d", len(X_train), len(X_val))

        # --- Fit Isolation Forest ---
        self.model = IsolationForest(
            n_estimators=self.n_estimators,
            max_samples=min(self.max_samples, len(X_train)),
            contamination=self.contamination,
            random_state=self.random_state,
            n_jobs=-1,
        )
        self.model.fit(X_train)
        logger.info("Isolation Forest fitted")

        # --- Save SHAP background from training data ---
        bg_size = min(self.shap_background_size, len(X_train))
        bg_idx = np.random.RandomState(self.random_state).choice(
            len(X_train), size=bg_size, replace=False
        )
        self.shap_background = X_train[bg_idx].copy()

        # --- Evaluate ---
        results = {}
        if attack_samples:
            results = self._evaluate(X_val, attack_samples)
            self._eval_results = results
        else:
            # At minimum, report self-consistency on normal validation data
            preds = self.model.predict(X_val)
            fp_rate = float(np.mean(preds == -1))
            results = {"normal_val_false_positive_rate": fp_rate}
            self._eval_results = results
            logger.info("Validation FP rate on normal data: %.3f", fp_rate)

        return results

    def _evaluate(
        self,
        X_val_normal: np.ndarray,
        attack_samples: List[MetricSample],
    ) -> Dict:
        """Evaluate on a mixed normal + attack validation set."""
        X_attack_raw = self._samples_to_matrix(attack_samples)
        X_attack = self.scaler.transform(X_attack_raw)

        X_val = np.vstack([X_val_normal, X_attack])
        # 0 = normal, 1 = attack (ground truth) — use int labels so
        # classification_report keys are "0"/"1" not "0.0"/"1.0"
        y_true = np.concatenate([
            np.zeros(len(X_val_normal), dtype=int),
            np.ones(len(X_attack), dtype=int),
        ])

        preds = self.model.predict(X_val)
        # IF: -1 = anomaly, 1 = normal → convert to 0/1
        y_pred = (preds == -1).astype(int)

        report = classification_report(y_true, y_pred, output_dict=True)
        cm = confusion_matrix(y_true, y_pred).tolist()

        # Log key metrics
        accuracy = report.get("accuracy", 0.0)
        attack_recall = report.get("1", {}).get("recall", 0.0)
        normal_precision = report.get("0", {}).get("precision", 0.0)
        logger.info("Evaluation — Accuracy: %.3f, Attack Recall: %.3f, Normal Precision: %.3f",
                     accuracy, attack_recall, normal_precision)

        return {
            "classification_report": report,
            "confusion_matrix": cm,
            "accuracy": accuracy,
            "attack_recall": attack_recall,
            "normal_false_positive_rate": 1.0 - normal_precision if normal_precision else 0.0,
        }

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #

    def save(self, path: str | Path) -> Path:
        """
        Save the trained model artifact.

        The artifact is a joblib dict containing everything needed by
        ``AnomalyDetector`` to run inference + xAI.
        """
        if self.model is None or self.scaler is None:
            raise RuntimeError("Call fit() before save()")

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        config = {
            "contamination": self.contamination,
            "n_estimators": self.n_estimators,
            "max_samples": self.max_samples,
            "random_state": self.random_state,
            "validation_split": self.validation_split,
        }

        artifact = {
            "model": self.model,
            "scaler": self.scaler,
            "feature_names": list(FEATURE_NAMES),
            "shap_background": self.shap_background,
            "training_timestamp": datetime.now().isoformat(),
            "training_config": config,
            "eval_results": self._eval_results,
        }

        joblib.dump(artifact, path)
        logger.info("Model saved to %s", path)
        return path
