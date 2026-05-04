"""Attack workflow configuration — all values from environment variables."""

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AttackConfig:
    """Configuration for the attack workflow frontend."""

    # Target
    target_url: str = field(
        default_factory=lambda: os.environ.get("TARGET_URL", "http://target-service:8080")
    )

    # Paths (inside the container)
    attacks_dir: str = field(
        default_factory=lambda: os.environ.get("ATTACKS_DIR", "/app/attacks")
    )
    configs_dir: str = field(
        default_factory=lambda: os.environ.get("CONFIGS_DIR", "/app/attacks/configs")
    )
    results_dir: str = field(
        default_factory=lambda: os.environ.get("RESULTS_DIR", "/app/results")
    )

    # Discovery
    discovery_file: str = field(
        default_factory=lambda: os.environ.get("DISCOVERY_FILE", "discovered-endpoints.json")
    )
    discovery_max_depth: int = field(
        default_factory=lambda: int(os.environ.get("DISCOVERY_MAX_DEPTH", "3"))
    )
    discovery_timeout: int = field(
        default_factory=lambda: int(os.environ.get("DISCOVERY_TIMEOUT", "5"))
    )

    # Attack defaults
    default_duration: int = field(
        default_factory=lambda: int(os.environ.get("DEFAULT_DURATION", "60"))
    )
    default_workers: int = field(
        default_factory=lambda: int(os.environ.get("DEFAULT_WORKERS", "10"))
    )
    default_mode: str = field(
        default_factory=lambda: os.environ.get("DEFAULT_MODE", "moderate")
    )
    default_pattern: str = field(
        default_factory=lambda: os.environ.get("DEFAULT_PATTERN", "constant")
    )

    # Prometheus (for monitoring attack impact)
    prometheus_url: str = field(
        default_factory=lambda: os.environ.get(
            "PROMETHEUS_URL", "http://prometheus-server.monitoring.svc.cluster.local:9090"
        )
    )

    # Frontend
    frontend_port: int = field(
        default_factory=lambda: int(os.environ.get("ATTACK_FRONTEND_PORT", "5001"))
    )
    secret_key: str = field(
        default_factory=lambda: os.environ.get("FLASK_SECRET_KEY", os.urandom(24).hex())
    )
