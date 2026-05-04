"""
SHAP-based explainer for anomaly detection decisions.

Separated from model.py so callers can use the detector without
the SHAP dependency if explanations are not needed.
"""

from __future__ import annotations

from typing import Tuple

import numpy as np
import shap

from .schema import Explanation, FeatureContribution
from .features import FEATURE_NAMES


class ShapExplainer:
    """
    Wraps a SHAP TreeExplainer for an Isolation Forest.

    The background data must come from the training pipeline (real scaled
    normal-traffic samples).  Using random noise produces meaningless
    explanations.
    """

    def __init__(self, model, background_data: np.ndarray):
        """
        Args:
            model: A fitted sklearn IsolationForest.
            background_data: 2-D array of scaled training samples,
                shape (n_background, n_features).  Stored at training time.
        """
        if background_data.ndim != 2:
            raise ValueError(
                f"background_data must be 2-D, got {background_data.ndim}-D"
            )
        if background_data.shape[1] != len(FEATURE_NAMES):
            raise ValueError(
                f"background_data has {background_data.shape[1]} features, "
                f"expected {len(FEATURE_NAMES)}"
            )

        self._explainer = shap.TreeExplainer(
            model,
            background_data,
            feature_perturbation="interventional",
        )
        self._base_value: float = float(
            np.atleast_1d(self._explainer.expected_value)[0]
        )

    @property
    def base_value(self) -> float:
        return self._base_value

    def explain(
        self,
        scaled_vector: np.ndarray,
        raw_features: np.ndarray,
        model_output: float,
    ) -> Explanation:
        """
        Generate a SHAP explanation for a single sample.

        Args:
            scaled_vector: 1-D or 2-D (1, n) scaled feature vector.
            raw_features: 1-D unscaled feature vector (for human readability).
            model_output: The anomaly score from the model.

        Returns:
            An Explanation with contributions sorted by |shap_value| descending.
        """
        x = np.atleast_2d(scaled_vector)
        sv = self._explainer.shap_values(x)

        # TreeExplainer may return (1, n) or (n,)
        if sv.ndim > 1:
            sv = sv[0]

        # Build contributions sorted by absolute SHAP magnitude
        raw_1d = np.atleast_1d(raw_features)
        contribs = []
        for i, name in enumerate(FEATURE_NAMES):
            contribs.append(
                FeatureContribution(
                    feature_name=name,
                    shap_value=float(sv[i]),
                    feature_value=float(raw_1d[i]),
                )
            )

        contribs.sort(key=lambda c: abs(c.shap_value), reverse=True)

        return Explanation(
            contributions=tuple(contribs),
            base_value=self._base_value,
            model_output=model_output,
        )
