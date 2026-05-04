"""
CICDDoS2019 dataset adapter.

Maps CICDDoS2019 flow-level features to the canonical MetricSample
used by the detection core.

Mapping notes
-------------
CICDDoS2019 is packet-capture data with 78 per-flow features extracted
by CICFlowMeter.  These are *not* identical to Istio L7 metrics, but
the statistical shape transfers well for anomaly detection:

  CICDDoS Feature         →  MetricSample field
  ───────────────────────────────────────────────
  Flow Packets/s          →  request_rate
  Flow IAT Std² / 1e12    →  request_rate_variance  (µs² → s²)
  Flow IAT Mean / 1000    →  latency_p50_ms (approximation)
  Fwd IAT Max  / 1000     →  latency_p95_ms
  Bwd IAT Max  / 1000     →  latency_p99_ms
  RST+FIN flags / total   →  error_rate  (proxy for failed connections)
  Flow Packets/s          →  total_request_rate
  Bwd Packet Length Mean  →  byte_rate_in (proxy)
  Flow Bytes/s            →  byte_rate_out
  Fwd Packet Length Mean  →  avg_request_size_bytes
  Avg Packet Size         →  avg_response_size_bytes
  SYN Flag Count scaled   →  connection_open_rate
  FIN Flag Count scaled   →  connection_close_rate

The important principle: we do NOT fabricate standard deviations as
constant fractions of the mean.  Where real variance is available in the
dataset, we use it; where it is not, we set the field to 0.

Dataset layout
--------------
The CICDDoS2019 dataset is split across multiple files by attack type,
with separate ``*-training.parquet`` (Jan 12 capture) and
``*-testing.parquet`` (Mar 11 capture) files.  Each file contains
78 numeric features plus a ``Label`` column (``Benign`` or an attack
name such as ``Syn``, ``DrDoS_DNS``, ``UDP``, etc.).

Supported attack categories::

    volumetric     : Syn, UDP, UDPLag, TFTP
    amplification  : DrDoS_DNS, DrDoS_NTP, DrDoS_SNMP, DrDoS_LDAP,
                     DrDoS_MSSQL, DrDoS_NetBIOS, DrDoS_SSDP, DrDoS_UDP
    protocol       : Portmap, NetBIOS, MSSQL, LDAP
    application    : WebDDoS
"""

from __future__ import annotations

import logging
import time
from typing import Dict, Iterator, List, Optional, Tuple

import numpy as np
import pandas as pd
from pathlib import Path

from ..core.schema import MetricSample

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
# Attack-type taxonomy
# ------------------------------------------------------------------ #

ATTACK_CATEGORIES: Dict[str, str] = {
    # Volumetric
    "syn": "volumetric",
    "udp": "volumetric",
    "udplag": "volumetric",
    "udp-lag": "volumetric",
    "tftp": "volumetric",
    # Amplification / reflection
    "drdos_dns": "amplification",
    "drdos_ntp": "amplification",
    "drdos_snmp": "amplification",
    "drdos_ldap": "amplification",
    "drdos_mssql": "amplification",
    "drdos_netbios": "amplification",
    "drdos_ssdp": "amplification",
    "drdos_udp": "amplification",
    # Protocol-level
    "portmap": "protocol",
    "netbios": "protocol",
    "mssql": "protocol",
    "ldap": "protocol",
    # Application-layer
    "webddos": "application",
}


def categorise_attack(label: str) -> str:
    """Map a CICDDoS2019 label string to an attack category."""
    key = label.strip().lower().replace(" ", "")
    return ATTACK_CATEGORIES.get(key, "unknown")


def _safe(row, col: str, default: float = 0.0) -> float:
    """Get a numeric value from a DataFrame row, handling missing / inf."""
    v = row.get(col, default)
    if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
        return default
    return float(v)


class CICDDoS2019Adapter:
    """
    Loads CICDDoS2019 CSV / Parquet files and yields MetricSample objects.

    Supports three loading modes:

    1. ``load()`` — load all files, return (normal, attack) lists
    2. ``load_split()`` — use *-training.parquet for train,
       *-testing.parquet for test, preserving the original dataset split
    3. ``load_by_attack()`` — return a dict of attack_label → samples,
       useful for per-attack-type evaluation

    Usage::

        adapter = CICDDoS2019Adapter("/path/to/dataset")

        # Mode 1: simple
        normal, attack = adapter.load()

        # Mode 2: respect train/test split
        (train_n, train_a), (test_n, test_a) = adapter.load_split()

        # Mode 3: per-attack evaluation
        by_attack = adapter.load_by_attack()
        for label, samples in by_attack.items():
            print(f"{label}: {len(samples)} flows")
    """

    def __init__(self, dataset_dir: str | Path):
        self.dataset_dir = Path(dataset_dir)
        if not self.dataset_dir.exists():
            raise FileNotFoundError(
                f"Dataset directory not found: {self.dataset_dir}"
            )

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def load(
        self,
        max_normal: int = 50_000,
        max_attack: int = 10_000,
        random_state: int = 42,
    ) -> Tuple[List[MetricSample], List[MetricSample]]:
        """
        Load the dataset and return (normal_samples, attack_samples).

        Samples are capped at ``max_normal`` / ``max_attack`` via random
        sampling to keep memory and training time bounded.
        """
        df = self._read_files()
        label_col = self._find_label_column(df)

        normal_df, attack_df = self._split_by_label(df, label_col)
        normal_df = self._cap(normal_df, max_normal, random_state)
        attack_df = self._cap(attack_df, max_attack, random_state)

        normal_samples = self._df_to_samples(normal_df)
        attack_samples = self._df_to_samples(attack_df)

        logger.info(
            "Loaded %d normal + %d attack samples from %s",
            len(normal_samples), len(attack_samples), self.dataset_dir,
        )
        return normal_samples, attack_samples

    def load_split(
        self,
        max_normal: int = 50_000,
        max_attack: int = 10_000,
        random_state: int = 42,
    ) -> Tuple[
        Tuple[List[MetricSample], List[MetricSample]],
        Tuple[List[MetricSample], List[MetricSample]],
    ]:
        """
        Load training and testing files separately, preserving the
        original CICDDoS2019 day-based split.

        Returns:
            ((train_normal, train_attack), (test_normal, test_attack))
        """
        train_df = self._read_files(pattern="*-training.*")
        test_df = self._read_files(pattern="*-testing.*")

        label_col_tr = self._find_label_column(train_df)
        label_col_te = self._find_label_column(test_df)

        tr_n, tr_a = self._split_by_label(train_df, label_col_tr)
        te_n, te_a = self._split_by_label(test_df, label_col_te)

        tr_n = self._cap(tr_n, max_normal, random_state)
        tr_a = self._cap(tr_a, max_attack, random_state)
        te_n = self._cap(te_n, max_normal, random_state + 1)
        te_a = self._cap(te_a, max_attack, random_state + 1)

        logger.info(
            "Train: %d normal + %d attack | Test: %d normal + %d attack",
            len(tr_n), len(tr_a), len(te_n), len(te_a),
        )

        return (
            (self._df_to_samples(tr_n), self._df_to_samples(tr_a)),
            (self._df_to_samples(te_n), self._df_to_samples(te_a)),
        )

    def load_by_attack(
        self,
        max_per_type: int = 2_000,
        random_state: int = 42,
    ) -> Dict[str, List[MetricSample]]:
        """
        Load all attack samples grouped by their label.

        Returns:
            dict mapping attack label (str) → list of MetricSample.
            A special key ``"Benign"`` holds the normal samples.
        """
        df = self._read_files()
        label_col = self._find_label_column(df)

        result: Dict[str, List[MetricSample]] = {}
        for label, group in df.groupby(label_col):
            label_str = str(label).strip()
            capped = self._cap(group, max_per_type, random_state)
            samples = self._df_to_samples(capped)
            if samples:
                result[label_str] = samples

        logger.info(
            "Loaded %d attack types: %s",
            len(result), list(result.keys()),
        )
        return result

    def load_aggregated(
        self,
        window_size: int = 50,
        max_normal: int = 50_000,
        max_attack: int = 10_000,
        random_state: int = 42,
        attack_ratio: float = 0.8,
    ) -> Tuple[List[MetricSample], List[MetricSample]]:
        """
        Load flows and aggregate them into time-window samples.

        Instead of treating each flow as a separate sample, this groups
        ``window_size`` consecutive flows into one MetricSample.  This
        mirrors how Prometheus collects aggregate metrics over 30-second
        windows and dramatically improves detection of volumetric attacks
        (Syn, UDP, TFTP) where individual flows look benign but the
        aggregate pattern is anomalous.

        Normal windows contain only benign flows.  Attack windows contain
        a mix of benign + attack flows (controlled by ``attack_ratio``),
        mimicking real-world conditions where attack flows are mixed with
        legitimate traffic.

        Args:
            window_size: Number of flows per aggregated sample.
            max_normal: Cap on normal aggregated samples.
            max_attack: Cap on attack aggregated samples.
            random_state: For reproducibility.
            attack_ratio: Fraction of attack flows per attack window
                (default 0.8 = 80% attack, 20% benign per window).

        Returns:
            (normal_samples, attack_samples) of aggregated MetricSamples.
        """
        df = self._read_files()
        label_col = self._find_label_column(df)
        normal_df, attack_df = self._split_by_label(df, label_col)

        rng = np.random.RandomState(random_state)

        # Aggregate normal windows (pure benign flows)
        normal_samples = self._aggregate_windows(
            normal_df, window_size, max_normal, rng, label="normal"
        )

        # Aggregate attack windows (mixed benign + attack)
        attack_samples = self._aggregate_attack_windows(
            normal_df, attack_df, label_col, window_size,
            max_attack, rng, attack_ratio,
        )

        logger.info(
            "Aggregated: %d normal + %d attack windows (window_size=%d)",
            len(normal_samples), len(attack_samples), window_size,
        )
        return normal_samples, attack_samples

    def load_aggregated_split(
        self,
        window_size: int = 50,
        max_normal: int = 50_000,
        max_attack: int = 10_000,
        random_state: int = 42,
        attack_ratio: float = 0.8,
    ) -> Tuple[
        Tuple[List[MetricSample], List[MetricSample]],
        Tuple[List[MetricSample], List[MetricSample]],
    ]:
        """
        Same as ``load_aggregated``, but split into train/test from
        the original dataset partition.

        Returns:
            ((train_normal, train_attack), (test_normal, test_attack))
        """
        rng = np.random.RandomState(random_state)

        train_df = self._read_files(pattern="*-training.*")
        test_df = self._read_files(pattern="*-testing.*")

        label_col_tr = self._find_label_column(train_df)
        label_col_te = self._find_label_column(test_df)

        tr_n_df, tr_a_df = self._split_by_label(train_df, label_col_tr)
        te_n_df, te_a_df = self._split_by_label(test_df, label_col_te)

        train_normal = self._aggregate_windows(
            tr_n_df, window_size, max_normal, rng, label="train_normal"
        )
        train_attack = self._aggregate_attack_windows(
            tr_n_df, tr_a_df, label_col_tr, window_size,
            max_attack, rng, attack_ratio,
        )
        test_normal = self._aggregate_windows(
            te_n_df, window_size, max_normal,
            np.random.RandomState(random_state + 1), label="test_normal",
        )
        test_attack = self._aggregate_attack_windows(
            te_n_df, te_a_df, label_col_te, window_size,
            max_attack, np.random.RandomState(random_state + 1),
            attack_ratio,
        )

        logger.info(
            "Aggregated split — Train: %d normal + %d attack | "
            "Test: %d normal + %d attack (window=%d)",
            len(train_normal), len(train_attack),
            len(test_normal), len(test_attack), window_size,
        )
        return (
            (train_normal, train_attack),
            (test_normal, test_attack),
        )

    def load_aggregated_by_attack(
        self,
        window_size: int = 50,
        max_per_type: int = 500,
        random_state: int = 42,
        attack_ratio: float = 0.8,
    ) -> Dict[str, List[MetricSample]]:
        """
        Load aggregated windows grouped by attack type.

        Each attack type gets windows containing that attack type's flows
        mixed with benign background traffic.

        Returns:
            dict mapping label → list of aggregated MetricSample.
        """
        df = self._read_files()
        label_col = self._find_label_column(df)
        normal_df, _ = self._split_by_label(df, label_col)

        rng = np.random.RandomState(random_state)
        result: Dict[str, List[MetricSample]] = {}

        # Add benign windows
        benign_windows = self._aggregate_windows(
            normal_df, window_size, max_per_type, rng, label="Benign"
        )
        if benign_windows:
            result["Benign"] = benign_windows

        # Per-attack-type windows
        for label, group in df.groupby(label_col):
            label_str = str(label).strip()
            if label_str.lower() == "benign":
                continue
            if len(group) < window_size // 2:
                continue  # too few flows for meaningful aggregation

            attack_windows = self._aggregate_attack_windows(
                normal_df, group, label_col, window_size,
                max_per_type, rng, attack_ratio,
            )
            if attack_windows:
                result[label_str] = attack_windows

        logger.info(
            "Aggregated by attack: %d types, %s",
            len(result), {k: len(v) for k, v in result.items()},
        )
        return result

    def summary(self) -> Dict[str, int]:
        """Return a quick summary: {label: count} without converting to MetricSample."""
        df = self._read_files()
        label_col = self._find_label_column(df)
        return df[label_col].value_counts().to_dict()

    # ------------------------------------------------------------------ #
    # Flow aggregation
    # ------------------------------------------------------------------ #

    @classmethod
    def _aggregate_windows(
        cls,
        df: pd.DataFrame,
        window_size: int,
        max_windows: int,
        rng: np.random.RandomState,
        label: str = "",
    ) -> List[MetricSample]:
        """Aggregate rows into windows of ``window_size`` flows."""
        if len(df) < window_size:
            return cls._df_to_samples(df)

        # Shuffle to remove temporal ordering bias
        indices = rng.permutation(len(df))
        n_windows = min(len(df) // window_size, max_windows)

        samples: List[MetricSample] = []
        for i in range(n_windows):
            start = i * window_size
            end = start + window_size
            window_indices = indices[start:end]
            window_df = df.iloc[window_indices]
            try:
                sample = cls._aggregate_flows(window_df)
                samples.append(sample)
            except Exception:
                continue

        return samples

    @classmethod
    def _aggregate_attack_windows(
        cls,
        normal_df: pd.DataFrame,
        attack_df: pd.DataFrame,
        label_col: str,
        window_size: int,
        max_windows: int,
        rng: np.random.RandomState,
        attack_ratio: float = 0.8,
    ) -> List[MetricSample]:
        """
        Create mixed windows: each has ``attack_ratio`` attack flows
        and ``1 - attack_ratio`` benign background flows.
        """
        n_attack_per_window = max(1, int(window_size * attack_ratio))
        n_benign_per_window = window_size - n_attack_per_window

        if len(attack_df) < n_attack_per_window:
            return cls._df_to_samples(attack_df)

        n_windows = min(
            len(attack_df) // n_attack_per_window,
            max_windows,
        )

        attack_indices = rng.permutation(len(attack_df))
        samples: List[MetricSample] = []

        for i in range(n_windows):
            # Attack portion
            a_start = i * n_attack_per_window
            a_end = a_start + n_attack_per_window
            a_idx = attack_indices[a_start:a_end]
            a_rows = attack_df.iloc[a_idx]

            # Benign background portion
            if len(normal_df) > 0 and n_benign_per_window > 0:
                b_idx = rng.choice(
                    len(normal_df), size=n_benign_per_window, replace=True
                )
                b_rows = normal_df.iloc[b_idx]
                window_df = pd.concat([a_rows, b_rows], ignore_index=True)
            else:
                window_df = a_rows

            try:
                sample = cls._aggregate_flows(window_df)
                samples.append(sample)
            except Exception:
                continue

        return samples

    @staticmethod
    def _aggregate_flows(window_df: pd.DataFrame) -> MetricSample:
        """
        Aggregate multiple CICDDoS2019 flow rows into a single
        MetricSample that resembles a Prometheus time-window sample.

        This computes window-level statistics from the individual flows:
          - request_rate = sum of all Flow Packets/s
          - request_rate_variance = variance of Flow Packets/s across flows
          - latency = percentiles of Flow IAT Mean across flows
          - byte rates = sum of Flow Bytes/s
          - error_rate = fraction of flows with RST flags
          - connection rates from SYN/FIN counts
          - size stats from mean packet sizes
        """
        def _col(col: str, default: float = 0.0) -> pd.Series:
            if col in window_df.columns:
                return window_df[col].replace(
                    [np.inf, -np.inf], np.nan
                ).fillna(default)
            return pd.Series([default] * len(window_df))

        n = len(window_df)

        # Aggregate packet/byte rates by summing across flows
        flow_pps = _col("Flow Packets/s", 0.001)
        total_request_rate = max(float(flow_pps.sum()), 0.001)
        request_rate_variance = float(flow_pps.var()) if n > 1 else 0.0

        # Latency from IAT distribution across flows
        iat_mean = _col("Flow IAT Mean") / 1000.0  # µs → ms
        latency_p50 = float(iat_mean.median())
        latency_p95 = float(iat_mean.quantile(0.95)) if n > 1 else latency_p50
        latency_p99 = float(iat_mean.quantile(0.99)) if n > 1 else latency_p95

        # Byte rates (sum across flows)
        byte_rate_out = float(_col("Flow Bytes/s").sum())
        byte_rate_in = float(_col("Bwd Packet Length Mean").mean()) * n

        # Packet sizes (mean across flows)
        avg_req_size = float(_col("Fwd Packet Length Mean").mean())
        avg_resp_col = "Avg Packet Size" if "Avg Packet Size" in window_df.columns \
            else "Packet Length Mean"
        avg_resp_size = float(_col(avg_resp_col).mean())

        # Error: fraction of flows with RST flags
        rst_counts = _col("RST Flag Count")
        error_rate = float((rst_counts > 0).sum()) / max(n, 1)

        # Connection rates from SYN/FIN counts (sum across flows)
        total_syn = float(_col("SYN Flag Count").sum())
        total_fin = float(_col("FIN Flag Count").sum())
        # Normalise by the mean flow duration to get rates
        dur_mean = float(_col("Flow Duration", 1.0).mean())
        dur_s = max(dur_mean / 1e6, 0.001)
        conn_open_rate = total_syn / dur_s
        conn_close_rate = total_fin / dur_s

        return MetricSample(
            timestamp=time.time(),
            request_rate=total_request_rate,
            request_rate_variance=request_rate_variance,
            latency_p50_ms=latency_p50,
            latency_p95_ms=latency_p95,
            latency_p99_ms=latency_p99,
            error_rate=error_rate,
            total_request_rate=total_request_rate,
            byte_rate_in=byte_rate_in,
            byte_rate_out=byte_rate_out,
            avg_request_size_bytes=avg_req_size,
            avg_response_size_bytes=avg_resp_size,
            connection_open_rate=conn_open_rate,
            connection_close_rate=conn_close_rate,
        )

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _read_files(self, pattern: str = "*") -> pd.DataFrame:
        """Read Parquet / CSV files matching *pattern* in dataset_dir."""
        parquets = sorted(self.dataset_dir.glob(f"{pattern}.parquet"))
        csvs = sorted(self.dataset_dir.glob(f"{pattern}.csv"))
        # If pattern didn't use extension, also try raw glob
        if not parquets and not csvs:
            parquets = sorted(self.dataset_dir.glob(pattern))
            parquets = [p for p in parquets if p.suffix in (".parquet", ".csv")]

        frames: list[pd.DataFrame] = []
        for pf in parquets:
            if "Zone.Identifier" in pf.name:
                continue
            frames.append(pd.read_parquet(pf))
        for cf in csvs:
            if "Zone.Identifier" in cf.name:
                continue
            frames.append(pd.read_csv(cf, low_memory=False))

        if not frames:
            raise FileNotFoundError(
                f"No .parquet or .csv files matching '{pattern}' "
                f"in {self.dataset_dir}"
            )

        combined = pd.concat(frames, ignore_index=True)
        combined.columns = combined.columns.str.strip()
        combined = combined.replace([np.inf, -np.inf], np.nan).fillna(0)
        return combined

    @staticmethod
    def _find_label_column(df: pd.DataFrame) -> str:
        for candidate in ("Label", " Label", "label"):
            stripped = candidate.strip()
            if candidate in df.columns:
                return candidate
            if stripped in df.columns:
                return stripped
        raise ValueError(
            f"No label column found. Columns: {list(df.columns[:20])}"
        )

    @staticmethod
    def _split_by_label(
        df: pd.DataFrame, label_col: str
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """Split into normal (Benign) and attack DataFrames."""
        is_benign = df[label_col].str.strip().str.lower() == "benign"
        return df[is_benign], df[~is_benign]

    @staticmethod
    def _cap(
        df: pd.DataFrame, max_rows: int, random_state: int
    ) -> pd.DataFrame:
        if len(df) > max_rows:
            return df.sample(n=max_rows, random_state=random_state)
        return df

    @classmethod
    def _df_to_samples(cls, df: pd.DataFrame) -> List[MetricSample]:
        """Vectorised conversion of a DataFrame to MetricSample list."""
        samples: List[MetricSample] = []
        for _, row in df.iterrows():
            try:
                samples.append(cls._row_to_sample(row))
            except Exception:
                continue  # skip corrupted rows
        return samples

    @staticmethod
    def _row_to_sample(row) -> MetricSample:
        """Map a single CICDDoS2019 row to a MetricSample."""
        flow_pps = max(_safe(row, "Flow Packets/s", 0.001), 0.001)
        flow_duration = _safe(row, "Flow Duration", 1.0)

        # Real variance from the dataset (IAT Std² approximates inter-arrival variance)
        iat_std = _safe(row, "Flow IAT Std")
        variance = (iat_std / 1e6) ** 2 if iat_std > 0 else 0.0

        # Error proxy: RST + FIN flags relative to total packets
        total_pkts = (
            _safe(row, "Total Fwd Packets", 0)
            + _safe(row, "Total Backward Packets", 0)
        )
        rst_count = _safe(row, "RST Flag Count")
        error_rate = rst_count / max(total_pkts, 1.0)

        # Connection rate proxies from TCP flags
        syn_count = _safe(row, "SYN Flag Count")
        fin_count = _safe(row, "FIN Flag Count")
        # Normalise by flow duration (microseconds → seconds)
        dur_s = max(flow_duration / 1e6, 0.001)
        conn_open_rate = syn_count / dur_s
        conn_close_rate = fin_count / dur_s

        return MetricSample(
            timestamp=time.time(),
            request_rate=flow_pps,
            request_rate_variance=variance,
            latency_p50_ms=_safe(row, "Flow IAT Mean") / 1000.0,
            latency_p95_ms=_safe(row, "Fwd IAT Max") / 1000.0,
            latency_p99_ms=_safe(row, "Bwd IAT Max") / 1000.0,
            error_rate=error_rate,
            total_request_rate=flow_pps,
            byte_rate_in=_safe(row, "Bwd Packet Length Mean"),
            byte_rate_out=_safe(row, "Flow Bytes/s"),
            avg_request_size_bytes=_safe(row, "Fwd Packet Length Mean"),
            avg_response_size_bytes=_safe(
                row, "Avg Packet Size",
                default=_safe(row, "Packet Length Mean"),
            ),
            connection_open_rate=conn_open_rate,
            connection_close_rate=conn_close_rate,
        )
