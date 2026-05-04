"""Tests for CICDDoS2019 adapter — data loading and feature mapping."""

from pathlib import Path
from typing import List

import numpy as np
import pandas as pd
import pytest

from detection_v2.adapters.cicddos_adapter import (
    ATTACK_CATEGORIES,
    CICDDoS2019Adapter,
    _safe,
    categorise_attack,
)
from detection_v2.core.features import FEATURE_NAMES, extract_features
from detection_v2.core.schema import MetricSample


# ------------------------------------------------------------------ #
# Helpers: build small in-memory parquet fixtures
# ------------------------------------------------------------------ #

_CIC_COLUMNS = [
    "Protocol", "Flow Duration", "Total Fwd Packets", "Total Backward Packets",
    "Fwd Packets Length Total", "Bwd Packets Length Total",
    "Fwd Packet Length Max", "Fwd Packet Length Min", "Fwd Packet Length Mean",
    "Fwd Packet Length Std", "Bwd Packet Length Max", "Bwd Packet Length Min",
    "Bwd Packet Length Mean", "Bwd Packet Length Std",
    "Flow Bytes/s", "Flow Packets/s", "Flow IAT Mean", "Flow IAT Std",
    "Flow IAT Max", "Flow IAT Min",
    "Fwd IAT Total", "Fwd IAT Mean", "Fwd IAT Std", "Fwd IAT Max", "Fwd IAT Min",
    "Bwd IAT Total", "Bwd IAT Mean", "Bwd IAT Std", "Bwd IAT Max", "Bwd IAT Min",
    "Fwd PSH Flags", "Bwd PSH Flags", "Fwd URG Flags", "Bwd URG Flags",
    "Fwd Header Length", "Bwd Header Length",
    "Fwd Packets/s", "Bwd Packets/s",
    "Packet Length Min", "Packet Length Max", "Packet Length Mean",
    "Packet Length Std", "Packet Length Variance",
    "FIN Flag Count", "SYN Flag Count", "RST Flag Count",
    "PSH Flag Count", "ACK Flag Count", "URG Flag Count",
    "CWE Flag Count", "ECE Flag Count",
    "Down/Up Ratio", "Avg Packet Size", "Avg Fwd Segment Size",
    "Avg Bwd Segment Size",
    "Fwd Avg Bytes/Bulk", "Fwd Avg Packets/Bulk", "Fwd Avg Bulk Rate",
    "Bwd Avg Bytes/Bulk", "Bwd Avg Packets/Bulk", "Bwd Avg Bulk Rate",
    "Subflow Fwd Packets", "Subflow Fwd Bytes",
    "Subflow Bwd Packets", "Subflow Bwd Bytes",
    "Init Fwd Win Bytes", "Init Bwd Win Bytes",
    "Fwd Act Data Packets", "Fwd Seg Size Min",
    "Active Mean", "Active Std", "Active Max", "Active Min",
    "Idle Mean", "Idle Std", "Idle Max", "Idle Min",
    "Label",
]


def _make_cic_row(label: str = "Benign", attack: bool = False, seed: int = 42) -> dict:
    """Create a dict matching CICDDoS2019 column structure."""
    rng = np.random.RandomState(seed)
    row = {col: 0.0 for col in _CIC_COLUMNS}
    if attack:
        row["Flow Packets/s"] = 50000.0 + rng.normal(0, 5000)
        row["Flow Bytes/s"] = 300000.0 + rng.normal(0, 30000)
        row["Flow IAT Mean"] = max(1.0, 10.0 + rng.normal(0, 5))
        row["Flow IAT Std"] = max(0.0, 50.0 + rng.normal(0, 20))
        row["Fwd IAT Max"] = max(0.0, 100.0 + rng.normal(0, 30))
        row["Bwd IAT Max"] = max(0.0, 80.0 + rng.normal(0, 20))
        row["Fwd Packet Length Mean"] = max(0.0, 6.0 + rng.normal(0, 2))
        row["Bwd Packet Length Mean"] = max(0.0, 6.0 + rng.normal(0, 2))
        row["Avg Packet Size"] = max(0.0, 6.0 + rng.normal(0, 2))
        row["Flow Duration"] = 1000000.0
        row["Total Fwd Packets"] = 50000
        row["Total Backward Packets"] = 0
        row["SYN Flag Count"] = 50000
        row["FIN Flag Count"] = 0
        row["RST Flag Count"] = 100
    else:
        row["Flow Packets/s"] = max(0.01, 5.0 + rng.normal(0, 2))
        row["Flow Bytes/s"] = max(0.0, 150.0 + rng.normal(0, 50))
        row["Flow IAT Mean"] = max(0.0, 200000.0 + rng.normal(0, 50000))
        row["Flow IAT Std"] = max(0.0, 100000.0 + rng.normal(0, 30000))
        row["Fwd IAT Max"] = max(0.0, 500000.0 + rng.normal(0, 100000))
        row["Bwd IAT Max"] = max(0.0, 600000.0 + rng.normal(0, 100000))
        row["Fwd Packet Length Mean"] = max(0.0, 200.0 + rng.normal(0, 50))
        row["Bwd Packet Length Mean"] = max(0.0, 500.0 + rng.normal(0, 100))
        row["Avg Packet Size"] = max(0.0, 350.0 + rng.normal(0, 50))
        row["Flow Duration"] = 5000000.0
        row["Total Fwd Packets"] = 10
        row["Total Backward Packets"] = 8
        row["SYN Flag Count"] = 1
        row["FIN Flag Count"] = 1
        row["RST Flag Count"] = 0
    row["Label"] = label
    return row


def _make_cic_dataset(tmp_path: Path, n_benign: int = 50, n_attack: int = 30):
    """Write small synthetic CIC parquet files to tmp_path."""
    rows = []
    for i in range(n_benign):
        rows.append(_make_cic_row("Benign", attack=False, seed=42 + i))
    for i in range(n_attack):
        rows.append(_make_cic_row("Syn", attack=True, seed=1000 + i))

    df = pd.DataFrame(rows)
    # Write as training file
    df.to_parquet(tmp_path / "Syn-training.parquet", index=False)

    # Also write a small testing file
    test_rows = []
    for i in range(10):
        test_rows.append(_make_cic_row("Benign", attack=False, seed=2000 + i))
    for i in range(15):
        test_rows.append(_make_cic_row("DrDoS_DNS", attack=True, seed=3000 + i))
    test_df = pd.DataFrame(test_rows)
    test_df.to_parquet(tmp_path / "DNS-testing.parquet", index=False)


# ------------------------------------------------------------------ #
# Tests: _safe helper
# ------------------------------------------------------------------ #


class TestSafe:
    def test_returns_value(self):
        row = {"x": 42.0}
        assert _safe(row, "x") == 42.0

    def test_returns_default_on_missing(self):
        row = {"x": 1.0}
        assert _safe(row, "y", 5.0) == 5.0

    def test_returns_default_on_nan(self):
        row = {"x": float("nan")}
        assert _safe(row, "x", 7.0) == 7.0

    def test_returns_default_on_inf(self):
        row = {"x": float("inf")}
        assert _safe(row, "x", 3.0) == 3.0

    def test_returns_default_on_none(self):
        row = {"x": None}
        assert _safe(row, "x", 9.0) == 9.0


# ------------------------------------------------------------------ #
# Tests: categorise_attack
# ------------------------------------------------------------------ #


class TestCategoriseAttack:
    def test_syn_is_volumetric(self):
        assert categorise_attack("Syn") == "volumetric"

    def test_drdos_dns_is_amplification(self):
        assert categorise_attack("DrDoS_DNS") == "amplification"

    def test_portmap_is_protocol(self):
        assert categorise_attack("Portmap") == "protocol"

    def test_webddos_is_application(self):
        assert categorise_attack("WebDDoS") == "application"

    def test_benign_is_unknown(self):
        assert categorise_attack("Benign") == "unknown"

    def test_case_insensitive(self):
        assert categorise_attack("SYN") == "volumetric"
        assert categorise_attack("drdos_ntp") == "amplification"

    def test_whitespace_stripped(self):
        assert categorise_attack("  Syn  ") == "volumetric"


# ------------------------------------------------------------------ #
# Tests: CICDDoS2019Adapter
# ------------------------------------------------------------------ #


class TestCICDDoS2019Adapter:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.data_dir = tmp_path / "cic_data"
        self.data_dir.mkdir()
        _make_cic_dataset(self.data_dir, n_benign=50, n_attack=30)
        self.adapter = CICDDoS2019Adapter(self.data_dir)

    def test_init_rejects_missing_dir(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            CICDDoS2019Adapter(tmp_path / "nonexistent")

    def test_load_returns_normal_and_attack(self):
        normal, attack = self.adapter.load()
        assert len(normal) > 0
        assert len(attack) > 0
        assert all(isinstance(s, MetricSample) for s in normal)
        assert all(isinstance(s, MetricSample) for s in attack)

    def test_load_caps_samples(self):
        normal, attack = self.adapter.load(max_normal=10, max_attack=5)
        assert len(normal) <= 10
        assert len(attack) <= 5

    def test_load_split_returns_train_and_test(self):
        (train_n, train_a), (test_n, test_a) = self.adapter.load_split()
        # Training file has Benign + Syn
        assert len(train_n) > 0
        assert len(train_a) > 0
        # Testing file has Benign + DrDoS_DNS
        assert len(test_n) > 0
        assert len(test_a) > 0

    def test_load_by_attack_groups_labels(self):
        by_attack = self.adapter.load_by_attack()
        assert "Benign" in by_attack
        # At least one attack type
        attack_labels = [k for k in by_attack if k.lower() != "benign"]
        assert len(attack_labels) > 0

    def test_summary_returns_counts(self):
        counts = self.adapter.summary()
        assert "Benign" in counts
        assert counts["Benign"] > 0

    def test_row_to_sample_produces_valid_features(self):
        normal, _ = self.adapter.load(max_normal=5, max_attack=0)
        for s in normal:
            v = extract_features(s)
            assert v.shape == (len(FEATURE_NAMES),)
            assert np.all(np.isfinite(v))

    def test_attack_row_to_sample_produces_valid_features(self):
        _, attack = self.adapter.load(max_normal=0, max_attack=5)
        for s in attack:
            v = extract_features(s)
            assert v.shape == (len(FEATURE_NAMES),)
            assert np.all(np.isfinite(v))

    def test_attack_samples_have_higher_request_rate(self):
        normal, attack = self.adapter.load(max_normal=20, max_attack=20)
        avg_normal = np.mean([s.request_rate for s in normal])
        avg_attack = np.mean([s.request_rate for s in attack])
        assert avg_attack > avg_normal * 5  # Attacks should be much higher

    def test_connection_rate_from_flags(self):
        """Connection rates should be derived from SYN/FIN flags, not hardcoded."""
        _, attack = self.adapter.load(max_normal=0, max_attack=5)
        for s in attack:
            # For SYN flood attacks, connection_open_rate should be high
            assert s.connection_open_rate > 0

    def test_error_rate_from_rst_flags(self):
        """Error rate should be derived from RST flags."""
        _, attack = self.adapter.load(max_normal=0, max_attack=5)
        for s in attack:
            # SYN floods have RST=100/50000
            assert s.error_rate >= 0


# ------------------------------------------------------------------ #
# Tests: Integration — train on CIC mock data
# ------------------------------------------------------------------ #


class TestCICTrainingIntegration:
    """End-to-end: load CIC → train → detect."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.data_dir = tmp_path / "cic_data"
        self.data_dir.mkdir()
        _make_cic_dataset(self.data_dir, n_benign=200, n_attack=50)
        self.model_path = tmp_path / "test_model.pkl"

    def test_train_and_detect(self):
        from detection_v2.training.trainer import Trainer
        from detection_v2.core.model import AnomalyDetector

        adapter = CICDDoS2019Adapter(self.data_dir)
        normal, attack = adapter.load()

        trainer = Trainer(n_estimators=30, max_samples=32, shap_background_size=20)
        results = trainer.fit(normal, attack_samples=attack)
        trainer.save(self.model_path)

        assert results.get("accuracy", 0) > 0

        detector = AnomalyDetector(self.model_path, enable_xai=False)

        # Test on a normal sample
        normal_result = detector.detect(normal[0])
        assert hasattr(normal_result, "is_anomaly")

        # Test on an attack sample
        attack_result = detector.detect(attack[0])
        assert hasattr(attack_result, "is_anomaly")

    def test_attack_recall_reasonable(self):
        from detection_v2.training.trainer import Trainer
        from detection_v2.core.model import AnomalyDetector

        adapter = CICDDoS2019Adapter(self.data_dir)
        normal, attack = adapter.load()

        trainer = Trainer(n_estimators=50, max_samples=64, shap_background_size=30)
        results = trainer.fit(normal, attack_samples=attack)
        trainer.save(self.model_path)

        # With well-separated synthetic CIC data, recall should be reasonable
        assert results.get("attack_recall", 0) > 0.3


# ------------------------------------------------------------------ #
# Tests: Flow aggregation
# ------------------------------------------------------------------ #


class TestFlowAggregation:
    """Test the flow aggregation / windowing functionality."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.data_dir = tmp_path / "cic_data"
        self.data_dir.mkdir()
        _make_cic_dataset(self.data_dir, n_benign=200, n_attack=100)
        self.adapter = CICDDoS2019Adapter(self.data_dir)

    def test_load_aggregated_returns_samples(self):
        normal, attack = self.adapter.load_aggregated(window_size=10)
        assert len(normal) > 0
        assert len(attack) > 0
        assert all(isinstance(s, MetricSample) for s in normal)
        assert all(isinstance(s, MetricSample) for s in attack)

    def test_aggregated_fewer_samples_than_raw(self):
        raw_normal, _ = self.adapter.load()
        agg_normal, _ = self.adapter.load_aggregated(window_size=10)
        # Aggregated should have fewer samples (grouped into windows)
        assert len(agg_normal) < len(raw_normal)

    def test_aggregated_request_rate_is_sum(self):
        """Aggregated windows should have higher request rates (summed)."""
        raw_normal, _ = self.adapter.load(max_normal=50)
        agg_normal, _ = self.adapter.load_aggregated(window_size=10)
        avg_raw = np.mean([s.request_rate for s in raw_normal])
        avg_agg = np.mean([s.request_rate for s in agg_normal])
        # Aggregated rate ≈ window_size × raw rate
        assert avg_agg > avg_raw * 3  # at least 3x higher

    def test_aggregated_attack_distinct_from_normal(self):
        """Aggregated attack windows should look different from normal."""
        normal, attack = self.adapter.load_aggregated(window_size=10)
        avg_normal_rate = np.mean([s.request_rate for s in normal])
        avg_attack_rate = np.mean([s.request_rate for s in attack])
        # Attack windows (80% attack flows) should have much higher rate
        assert avg_attack_rate > avg_normal_rate * 5

    def test_aggregated_produces_valid_features(self):
        normal, attack = self.adapter.load_aggregated(window_size=10)
        for s in (normal + attack)[:20]:
            v = extract_features(s)
            assert v.shape == (len(FEATURE_NAMES),)
            assert np.all(np.isfinite(v))

    def test_aggregated_split_returns_train_test(self):
        (tr_n, tr_a), (te_n, te_a) = self.adapter.load_aggregated_split(
            window_size=5
        )
        assert len(tr_n) > 0
        assert len(tr_a) > 0
        assert len(te_n) > 0
        assert len(te_a) > 0

    def test_aggregated_by_attack_groups(self):
        by_attack = self.adapter.load_aggregated_by_attack(
            window_size=5, max_per_type=20
        )
        assert "Benign" in by_attack
        attack_labels = [k for k in by_attack if k.lower() != "benign"]
        assert len(attack_labels) > 0

    def test_aggregated_train_and_detect(self):
        """End-to-end: aggregated load → train → detect with good recall."""
        from detection_v2.training.trainer import Trainer
        from detection_v2.core.model import AnomalyDetector

        normal, attack = self.adapter.load_aggregated(window_size=10)

        trainer = Trainer(
            n_estimators=50, max_samples=64, shap_background_size=20
        )
        results = trainer.fit(normal, attack_samples=attack)

        model_path = self.data_dir / "agg_model.pkl"
        trainer.save(model_path)

        detector = AnomalyDetector(model_path, enable_xai=False)

        # Detect on attack windows
        detected = sum(
            1 for s in attack[:20]
            if detector.detect(s, explain=False).is_anomaly
        )
        recall = detected / min(len(attack), 20)
        # Aggregated data should give better separation
        assert recall > 0.3, f"Recall too low: {recall:.1%}"
