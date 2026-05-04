"""Adapters for feeding external data into the core detection pipeline."""

from .prometheus_adapter import PrometheusAdapter
from .cicddos_adapter import (
    ATTACK_CATEGORIES,
    CICDDoS2019Adapter,
    categorise_attack,
)

__all__ = [
    "PrometheusAdapter",
    "CICDDoS2019Adapter",
    "ATTACK_CATEGORIES",
    "categorise_attack",
]
