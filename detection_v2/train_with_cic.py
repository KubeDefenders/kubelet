#!/usr/bin/env python3
"""
Train and evaluate the anomaly detector using the CICDDoS2019 dataset.

This script provides a complete workflow:
  1. Load CICDDoS2019 data (respecting the original train/test split)
  2. Train an Isolation Forest on benign (normal) traffic
  3. Evaluate on held-out test data with per-attack-type breakdown
  4. Save the model artifact and evaluation report

Usage::

    # Default: use existing dataset at detection/ml-detector/data/ciddos2019
    python -m detection_v2.train_with_cic

    # Custom dataset path
    python -m detection_v2.train_with_cic --dataset-dir /path/to/cicddos2019

    # Tune parameters
    python -m detection_v2.train_with_cic --contamination 0.03 --n-estimators 300

    # Quick mode (fewer samples for fast iteration)
    python -m detection_v2.train_with_cic --quick

    # Aggregated mode (groups flows into windows — better for volumetric attacks)
    python -m detection_v2.train_with_cic --aggregate --window-size 50
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

from .adapters.cicddos_adapter import CICDDoS2019Adapter, categorise_attack
from .core.features import FEATURE_NAMES, extract_features
from .core.model import AnomalyDetector
from .core.schema import MetricSample
from .training.trainer import Trainer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #


def _detect_dataset_dir() -> Path:
    """Auto-detect the CICDDoS2019 dataset directory."""
    candidates = [
        Path("detection/ml-detector/data/ciddos2019"),
        Path("data/ciddos2019"),
        Path("data/cicddos2019"),
        Path("/data/cicddos2019"),
    ]
    for p in candidates:
        if p.exists() and any(p.glob("*.parquet")):
            return p
    raise FileNotFoundError(
        "Could not auto-detect CICDDoS2019 dataset directory. "
        "Use --dataset-dir to specify it."
    )


def _per_attack_evaluation(
    detector: AnomalyDetector,
    samples_by_label: Dict[str, List[MetricSample]],
) -> Dict[str, Dict]:
    """
    Evaluate the trained detector on each attack type individually.

    Returns a dict of label → {count, detected, recall, category,
    avg_score, min_score}.
    """
    results: Dict[str, Dict] = {}

    for label, samples in sorted(samples_by_label.items()):
        if not samples:
            continue

        scores = []
        detected = 0
        for s in samples:
            try:
                r = detector.detect(s, explain=False)
                scores.append(r.anomaly_score)
                if r.is_anomaly:
                    detected += 1
            except Exception:
                continue

        if not scores:
            continue

        recall = detected / len(scores) if scores else 0.0
        category = categorise_attack(label)

        results[label] = {
            "count": len(scores),
            "detected": detected,
            "recall": recall,
            "category": category,
            "avg_score": float(np.mean(scores)),
            "min_score": float(np.min(scores)),
        }

    return results


def _print_report(
    train_results: Dict,
    per_attack: Dict[str, Dict],
    normal_fp_rate: float,
    elapsed_s: float,
) -> str:
    """Format and print a human-readable report. Returns the report text."""
    lines = []
    lines.append("")
    lines.append("=" * 72)
    lines.append("  CICDDoS2019 Training & Evaluation Report")
    lines.append("=" * 72)
    lines.append("")

    # Training summary
    lines.append("Training Results:")
    for k, v in sorted(train_results.items()):
        if k == "classification_report":
            continue
        if isinstance(v, float):
            lines.append(f"  {k:40s}  {v:.4f}")
        else:
            lines.append(f"  {k:40s}  {v}")
    lines.append("")

    lines.append(f"Normal traffic false positive rate:  {normal_fp_rate:.4f} "
                 f"({normal_fp_rate*100:.1f}%)")
    lines.append("")

    # Per-attack table
    lines.append("Per-Attack Detection Rates:")
    lines.append(f"  {'Label':<22s}  {'Category':<15s}  {'Count':>6s}  "
                 f"{'Detect':>6s}  {'Recall':>7s}  {'Avg Score':>10s}")
    lines.append("  " + "-" * 70)

    total_attacks = 0
    total_detected = 0
    category_stats: Dict[str, Dict] = {}

    for label, info in sorted(per_attack.items()):
        if label.lower().strip() == "benign":
            continue
        lines.append(
            f"  {label:<22s}  {info['category']:<15s}  "
            f"{info['count']:6d}  {info['detected']:6d}  "
            f"{info['recall']:7.1%}  {info['avg_score']:+10.4f}"
        )
        total_attacks += info["count"]
        total_detected += info["detected"]

        cat = info["category"]
        if cat not in category_stats:
            category_stats[cat] = {"count": 0, "detected": 0}
        category_stats[cat]["count"] += info["count"]
        category_stats[cat]["detected"] += info["detected"]

    lines.append("  " + "-" * 70)
    overall_recall = total_detected / max(total_attacks, 1)
    lines.append(
        f"  {'OVERALL':<22s}  {'':15s}  {total_attacks:6d}  "
        f"{total_detected:6d}  {overall_recall:7.1%}"
    )
    lines.append("")

    # Category summary
    lines.append("Detection by Category:")
    for cat in sorted(category_stats):
        s = category_stats[cat]
        r = s["detected"] / max(s["count"], 1)
        lines.append(f"  {cat:<20s}  {s['detected']:5d}/{s['count']:<5d}  {r:7.1%}")
    lines.append("")
    lines.append(f"Total time: {elapsed_s:.1f}s")
    lines.append("=" * 72)

    report = "\n".join(lines)
    print(report)
    return report


# ------------------------------------------------------------------ #
# Main workflow
# ------------------------------------------------------------------ #


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Train anomaly detector on CICDDoS2019 dataset",
    )
    parser.add_argument(
        "--dataset-dir",
        type=str,
        default=None,
        help="Path to CICDDoS2019 parquet/csv files",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="results/cic_training",
        help="Directory to save model and reports",
    )
    parser.add_argument(
        "--contamination",
        type=float,
        default=0.05,
        help="Isolation Forest contamination parameter",
    )
    parser.add_argument(
        "--n-estimators",
        type=int,
        default=200,
        help="Number of trees in the Isolation Forest",
    )
    parser.add_argument(
        "--max-samples",
        type=int,
        default=256,
        help="Samples per tree",
    )
    parser.add_argument(
        "--max-normal",
        type=int,
        default=50_000,
        help="Cap on normal training samples",
    )
    parser.add_argument(
        "--max-attack",
        type=int,
        default=10_000,
        help="Cap on attack evaluation samples",
    )
    parser.add_argument(
        "--aggregate",
        action="store_true",
        help="Aggregate flows into time windows (recommended for volumetric attacks)",
    )
    parser.add_argument(
        "--window-size",
        type=int,
        default=50,
        help="Flows per aggregated window (only with --aggregate)",
    )
    parser.add_argument(
        "--attack-ratio",
        type=float,
        default=0.8,
        help="Fraction of attack flows per attack window (only with --aggregate)",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Quick mode: fewer samples for fast iteration",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed",
    )
    args = parser.parse_args()

    t0 = time.time()

    # Quick mode overrides
    if args.quick:
        args.max_normal = 5_000
        args.max_attack = 1_000
        args.n_estimators = 50

    # ── 1. Locate dataset ───────────────────────────────────────────
    if args.dataset_dir:
        dataset_dir = Path(args.dataset_dir)
    else:
        dataset_dir = _detect_dataset_dir()
    logger.info("Dataset directory: %s", dataset_dir)

    adapter = CICDDoS2019Adapter(dataset_dir)

    # ── 2. Load data (respect train/test split) ────────────────────
    mode = "aggregated" if args.aggregate else "per-flow"
    logger.info("Loading CICDDoS2019 dataset (%s mode) ...", mode)

    if args.aggregate:
        (train_normal, train_attack), (test_normal, test_attack) = (
            adapter.load_aggregated_split(
                window_size=args.window_size,
                max_normal=args.max_normal,
                max_attack=args.max_attack,
                random_state=args.seed,
                attack_ratio=args.attack_ratio,
            )
        )
    else:
        (train_normal, train_attack), (test_normal, test_attack) = (
            adapter.load_split(
                max_normal=args.max_normal,
                max_attack=args.max_attack,
                random_state=args.seed,
            )
        )
    logger.info(
        "Train: %d normal, %d attack | Test: %d normal, %d attack",
        len(train_normal), len(train_attack),
        len(test_normal), len(test_attack),
    )

    # ── 3. Train ────────────────────────────────────────────────────
    logger.info("Training Isolation Forest ...")
    trainer = Trainer(
        contamination=args.contamination,
        n_estimators=args.n_estimators,
        max_samples=args.max_samples,
        random_state=args.seed,
    )
    # Combine train+test attack samples for evaluation during training
    all_attack_for_eval = train_attack + test_attack
    train_results = trainer.fit(
        train_normal,
        attack_samples=all_attack_for_eval[:args.max_attack],
    )
    logger.info("Training complete — attack recall: %.3f",
                train_results.get("attack_recall", 0))

    # ── 4. Save model ──────────────────────────────────────────────
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    model_path = output_dir / "cicddos_detector.pkl"
    trainer.save(model_path)
    logger.info("Model saved: %s", model_path)

    # ── 5. Load & evaluate on test set ─────────────────────────────
    logger.info("Evaluating on test set ...")
    detector = AnomalyDetector(model_path, enable_xai=False)

    # Normal FP rate
    normal_fp = 0
    for s in test_normal:
        try:
            r = detector.detect(s, explain=False)
            if r.is_anomaly:
                normal_fp += 1
        except Exception:
            pass
    normal_fp_rate = normal_fp / max(len(test_normal), 1)

    # Per-attack evaluation on test attacks
    if args.aggregate:
        test_by_label = adapter.load_aggregated_by_attack(
            window_size=args.window_size,
            max_per_type=min(args.max_attack, 500),
            random_state=args.seed,
            attack_ratio=args.attack_ratio,
        )
    else:
        test_by_label = adapter.load_by_attack(
            max_per_type=min(args.max_attack, 2000),
            random_state=args.seed,
        )
    per_attack = _per_attack_evaluation(detector, test_by_label)

    # ── 6. Report ──────────────────────────────────────────────────
    elapsed = time.time() - t0
    report_text = _print_report(
        train_results, per_attack, normal_fp_rate, elapsed
    )

    # Save JSON report
    report = {
        "timestamp": datetime.now().isoformat(),
        "dataset_dir": str(dataset_dir),
        "model_path": str(model_path),
        "training_config": {
            "contamination": args.contamination,
            "n_estimators": args.n_estimators,
            "max_samples": args.max_samples,
            "max_normal": args.max_normal,
            "max_attack": args.max_attack,
            "seed": args.seed,
            "aggregate": args.aggregate,
            "window_size": args.window_size if args.aggregate else None,
            "attack_ratio": args.attack_ratio if args.aggregate else None,
        },
        "training_results": {
            k: v for k, v in train_results.items()
            if k != "classification_report"
        },
        "test_normal_fpr": normal_fp_rate,
        "per_attack_results": per_attack,
        "elapsed_seconds": elapsed,
    }
    report_path = output_dir / "cic_evaluation_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    logger.info("Report saved: %s", report_path)

    # Save report text
    text_path = output_dir / "cic_evaluation_report.txt"
    with open(text_path, "w") as f:
        f.write(report_text)

    logger.info("Done in %.1fs", elapsed)


if __name__ == "__main__":
    main()
