"""
Anomaly detection model wrapper.

Encapsulates:
  - Model loading (from a training artifact)
  - Feature scaling
  - Inference (predict + decision_function)
  - Optional SHAP explanation via ShapExplainer
  - Feature-vector validation
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Optional

import joblib
import numpy as np

from .schema import DetectionResult, MetricSample, Severity
from .features import FEATURE_NAMES, extract_features, validate_feature_vector
from .errors import ModelLoadError, FeatureValidationError
from .explainer import ShapExplainer


# ---------------------------------------------------------------------------
# Severity classification — single source of truth
# ---------------------------------------------------------------------------
_DEFAULT_SEVERITY_THRESHOLDS = {
    Severity.CRITICAL: -0.7,
    Severity.HIGH: -0.5,
    Severity.MEDIUM: -0.3,
    # Everything above -0.3 is LOW
}


def classify_severity(
    score: float,
    thresholds: Optional[dict] = None,
) -> Severity:
    """Map an anomaly score (lower = more anomalous) to a Severity."""
    t = thresholds or _DEFAULT_SEVERITY_THRESHOLDS
    if score < t.get(Severity.CRITICAL, -0.7):
        return Severity.CRITICAL
    if score < t.get(Severity.HIGH, -0.5):
        return Severity.HIGH
    if score < t.get(Severity.MEDIUM, -0.3):
        return Severity.MEDIUM
    return Severity.LOW


# ---------------------------------------------------------------------------
# Model artifact keys (contract between trainer and detector)
# ---------------------------------------------------------------------------
_REQUIRED_KEYS = {"model", "scaler", "feature_names", "shap_background"}


class AnomalyDetector:
    """
    Loads a trained model artifact and performs inference.

    Artifact format (joblib dict)::

        {
            "model": IsolationForest,
            "scaler": RobustScaler,
            "feature_names": list[str],
            "shap_background": np.ndarray,   # shape (n, 16)
            "training_timestamp": str,        # ISO-8601
            "training_config": dict,          # optional
        }
    """

    def __init__(
        self,
        model_path: str | Path,
        *,
        severity_thresholds: Optional[dict] = None,
        enable_xai: bool = True,
    ):
        model_path = Path(model_path)
        if not model_path.exists():
            raise ModelLoadError(f"Model file not found: {model_path}")

        try:
            artifact = joblib.load(model_path)
        except Exception as exc:
            raise ModelLoadError(f"Failed to load model: {exc}") from exc

        missing = _REQUIRED_KEYS - set(artifact.keys())
        if missing:
            raise ModelLoadError(
                f"Model artifact is missing required keys: {missing}"
            )

        stored_names = artifact["feature_names"]
        if list(stored_names) != list(FEATURE_NAMES):
            raise ModelLoadError(
                f"Feature name mismatch.\n"
                f"  Model expects: {stored_names}\n"
                f"  Code defines:  {list(FEATURE_NAMES)}"
            )

        self._model = artifact["model"]
        self._scaler = artifact["scaler"]
        self._severity_thresholds = severity_thresholds

        # Unique ID for alert provenance
        with open(model_path, "rb") as f:
            self._model_version = hashlib.sha256(f.read()).hexdigest()[:12]

        # SHAP (optional)
        self._explainer: Optional[ShapExplainer] = None
        if enable_xai:
            bg = artifact["shap_background"]
            self._explainer = ShapExplainer(self._model, bg)

    @property
    def model_version(self) -> str:
        return self._model_version

    # --------------------------------------------------------------------- #
    # Inference
    # --------------------------------------------------------------------- #

    def detect(
        self,
        sample: MetricSample,
        *,
        explain: bool = False,
    ) -> DetectionResult:
        """
        Run inference on one MetricSample.

        Args:
            sample: The metrics collected from any source.
            explain: If True (and xAI is enabled), generate a SHAP
                explanation *only when an anomaly is detected*.

        Returns:
            DetectionResult (immutable).

        Raises:
            FeatureValidationError: If the feature vector is invalid.
        """
        t0 = time.monotonic()

        # 1. Extract features (pure function)
        raw_features = extract_features(sample)

        # 2. Scale
        scaled = self._scaler.transform(raw_features.reshape(1, -1))
        scaled_1d = scaled[0]

        # 3. Validate (may raise)
        validate_feature_vector(scaled_1d)

        # 4. Inference
        score = float(self._model.decision_function(scaled)[0])
        is_anomaly = bool(self._model.predict(scaled)[0] == -1)
        severity = classify_severity(score, self._severity_thresholds)

        # 5. Explain (only anomalies, only when requested + explainer present)
        explanation = None
        if explain and is_anomaly and self._explainer is not None:
            explanation = self._explainer.explain(
                scaled_1d, raw_features, score
            )

        elapsed_ms = (time.monotonic() - t0) * 1000.0

        return DetectionResult(
            is_anomaly=is_anomaly,
            anomaly_score=score,
            severity=severity,
            timestamp=sample.timestamp,
            feature_names=tuple(FEATURE_NAMES),
            scaled_features=scaled_1d.copy(),
            explanation=explanation,
            model_version=self._model_version,
            detection_latency_ms=elapsed_ms,
        )
