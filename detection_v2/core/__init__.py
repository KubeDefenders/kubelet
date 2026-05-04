"""Core detection logic — no dependency on Prometheus, Istio, or any project infrastructure."""

from .schema import MetricSample, DetectionResult, Explanation, FeatureContribution, Severity
from .features import extract_features, FEATURE_NAMES
from .errors import MetricCollectionError, FeatureValidationError, ModelLoadError
from .model import AnomalyDetector

__all__ = [
    "MetricSample",
    "DetectionResult",
    "Explanation",
    "FeatureContribution",
    "Severity",
    "extract_features",
    "FEATURE_NAMES",
    "MetricCollectionError",
    "FeatureValidationError",
    "ModelLoadError",
    "AnomalyDetector",
]
