"""
Domain-specific error types.

Using typed exceptions (rather than generic RuntimeError) lets callers
implement precise recovery logic.
"""


class MetricCollectionError(Exception):
    """
    Raised by metric adapters when data cannot be collected reliably.

    Callers should skip the detection cycle but continue monitoring.
    Do NOT classify as an attack or as normal traffic.
    """


class FeatureValidationError(Exception):
    """
    Raised when an extracted feature vector fails sanity checks.

    Callers should log the failure, skip inference, and increment a
    validation-failures counter.
    """


class ModelLoadError(Exception):
    """
    Raised when a model artifact cannot be loaded or is incompatible.

    This is a fatal startup error — callers should not attempt recovery.
    """
