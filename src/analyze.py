#!/usr/bin/env python3
"""
Unified Analysis Tool for DDoS Experiments

Modes:
- summary: Quick summary of single experiment
- aggregate: Aggregate multiple experiments
- batch: Batch analysis with comparisons
- stats: Detailed statistics

Usage:
    # Quick summary
    python analyze.py summary results.json
    
    # Aggregate multiple
    python analyze.py aggregate experiment_1/results.json experiment_2/results.json
    
    # Batch analysis
    python analyze.py batch ../results/experiments/continuous_*
    
    # Auto-detect latest
    python analyze.py summary
"""

import json
import sys
import argparse
import glob
from pathlib import Path
from typing import List, Dict
from datetime import datetime


class ExperimentAnalyzer:
    """Unified analyzer for all experiment types"""
    
    def __init__(self):
        self.experiments = []
        
    def load_experiment(self, json_path: str) -> Dict:
        """Load single experiment results"""
        with open(json_path) as f:
            return json.load(f)
            
    def find_latest_experiment(self) -> Path:
        """Find most recent experiment"""
        results_dir = Path('../results/experiments')
        if not results_dir.exists():
            raise FileNotFoundError("No experiments directory found")
            
        exp_dirs = sorted(results_dir.glob('*_*'), reverse=True)
        if not exp_dirs:
            raise FileNotFoundError("No experiments found")
            
        for exp_dir in exp_dirs:
            results_file = exp_dir / 'results.json'
            if results_file.exists():
                return results_file
                
        raise FileNotFoundError("No results.json found")
        
    def print_summary(self, data: Dict):
        """Print summary of single experiment"""
        meta = data['metadata']
        stats = data['statistics']
        
        print("=" * 80)
        print("EXPERIMENT SUMMARY")
        print("=" * 80)
        print(f"Mode:     {meta.get('mode', 'N/A')}")
        if meta.get('experiment_start'):
            print(f"Start:    {meta['experiment_start'][:19]}")
        if meta.get('experiment_end'):
            print(f"End:      {meta['experiment_end'][:19]}")
        print(f"Target:   {meta['target_url']}")
        print()
        
        # Overall stats
        overall = stats['overall']
        print("=" * 80)
        print("OVERALL DETECTION PERFORMANCE")
        print("=" * 80)
        print(f"Total Attacks:     {overall['total_attacks']}")
        print(f"Detected:          {overall['detected']} ({overall['detection_rate']*100:.1f}%)")
        print(f"Missed:            {overall['missed']} ({(1-overall['detection_rate'])*100:.1f}%)")
        print()
        
        # Per-attack-type table
        print("=" * 80)
        print("PER ATTACK TYPE STATISTICS")
        print("=" * 80)
        print(f"{'Attack Type':<15} {'Total':>6} {'Detected':>9} {'Missed':>7} {'Rate':>7} {'Avg Latency':>12}")
        print("-" * 80)
        for attack_type, type_stats in stats['by_attack_type'].items():
            rate = type_stats['detection_rate'] * 100
            latency = f"{type_stats['mean_latency']:.1f}s" if type_stats['mean_latency'] > 0 else "N/A"
            print(f"{attack_type:<15} {type_stats['total']:>6} {type_stats['detected']:>9} "
                  f"{type_stats['missed']:>7} {rate:>6.1f}% {latency:>12}")
        print()
        
        # Individual attacks
        if data.get('attacks'):
            print("=" * 80)
            print("INDIVIDUAL ATTACKS")
            print("=" * 80)
            print(f"{'ID':<12} {'Type':<15} {'Start':<8} {'Duration':>8} {'Rate':>6} {'Detected':>9} {'Latency':>9}")
            print("-" * 80)
            for attack in data['attacks']:
                start_time = attack['start_time'][11:19] if len(attack['start_time']) > 19 else attack['start_time']
                detected = "✓ Yes" if attack['detected'] else "✗ No"
                latency = f"{attack['detection_latency_sec']:.1f}s" if attack['detection_latency_sec'] else "N/A"
                print(f"{attack['attack_id']:<12} {attack['attack_type']:<15} {start_time:<8} "
                      f"{attack['duration_seconds']:>7}s {attack['total_rate']:>6} {detected:>9} {latency:>9}")
            print()
        
        # Latency stats
        if stats.get('detection_latencies'):
            latencies = stats['detection_latencies']
            print("=" * 80)
            print("DETECTION LATENCY STATISTICS")
            print("=" * 80)
            print(f"Mean:   {sum(latencies)/len(latencies):.2f}s")
            print(f"Min:    {min(latencies):.2f}s")
            print(f"Max:    {max(latencies):.2f}s")
            sorted_lat = sorted(latencies)
            median = sorted_lat[len(sorted_lat)//2] if len(sorted_lat) % 2 == 1 else (sorted_lat[len(sorted_lat)//2-1] + sorted_lat[len(sorted_lat)//2])/2
            print(f"Median: {median:.2f}s")
            print("=" * 80)
            
    def aggregate_experiments(self, experiments: List[Dict]) -> Dict:
        """Aggregate multiple experiments"""
        total_attacks = 0
        total_detected = 0
        all_latencies = []
        attack_type_stats = {}
        
        for exp in experiments:
            stats = exp['statistics']
            total_attacks += stats['overall']['total_attacks']
            total_detected += stats['overall']['detected']
            
            if stats.get('detection_latencies'):
                all_latencies.extend(stats['detection_latencies'])
                
            for attack_type, type_stats in stats['by_attack_type'].items():
                if attack_type not in attack_type_stats:
                    attack_type_stats[attack_type] = {
                        'total': 0, 'detected': 0, 'latencies': []
                    }
                attack_type_stats[attack_type]['total'] += type_stats['total']
                attack_type_stats[attack_type]['detected'] += type_stats['detected']
                
        aggregated = {
            'num_experiments': len(experiments),
            'total_attacks': total_attacks,
            'total_detected': total_detected,
            'overall_detection_rate': total_detected / total_attacks if total_attacks > 0 else 0,
            'by_attack_type': {}
        }
        
        for attack_type, stats in attack_type_stats.items():
            aggregated['by_attack_type'][attack_type] = {
                'total': stats['total'],
                'detected': stats['detected'],
                'detection_rate': stats['detected'] / stats['total'] if stats['total'] > 0 else 0
            }
            
        if all_latencies:
            aggregated['latency_mean'] = sum(all_latencies) / len(all_latencies)
            aggregated['latency_min'] = min(all_latencies)
            aggregated['latency_max'] = max(all_latencies)
            
        return aggregated
        
    def print_aggregated(self, aggregated: Dict):
        """Print aggregated results"""
        print("=" * 80)
        print(f"AGGREGATED RESULTS ({aggregated['num_experiments']} experiments)")
        print("=" * 80)
        print(f"Total Attacks:     {aggregated['total_attacks']}")
        print(f"Total Detected:    {aggregated['total_detected']} ({aggregated['overall_detection_rate']*100:.1f}%)")
        print()
        
        print("=" * 80)
        print("PER ATTACK TYPE (AGGREGATED)")
        print("=" * 80)
        print(f"{'Attack Type':<15} {'Total':>8} {'Detected':>10} {'Rate':>8}")
        print("-" * 80)
        for attack_type, stats in aggregated['by_attack_type'].items():
            print(f"{attack_type:<15} {stats['total']:>8} {stats['detected']:>10} {stats['detection_rate']*100:>7.1f}%")
        print()
        
        if 'latency_mean' in aggregated:
            print("=" * 80)
            print("AGGREGATED LATENCY STATISTICS")
            print("=" * 80)
            print(f"Mean:   {aggregated['latency_mean']:.2f}s")
            print(f"Min:    {aggregated['latency_min']:.2f}s")
            print(f"Max:    {aggregated['latency_max']:.2f}s")
            print("=" * 80)
            
    def batch_analysis(self, experiment_paths: List[str]):
        """Batch analysis with comparison"""
        experiments = [self.load_experiment(p) for p in experiment_paths]
        
        print("=" * 80)
        print(f"BATCH ANALYSIS ({len(experiments)} experiments)")
        print("=" * 80)
        
        # Individual experiment summaries
        for i, (path, exp) in enumerate(zip(experiment_paths, experiments), 1):
            stats = exp['statistics']
            overall = stats['overall']
            exp_name = Path(path).parent.name
            
            print(f"\n{i}. {exp_name}")
            print(f"   Total: {overall['total_attacks']}, "
                  f"Detected: {overall['detected']}, "
                  f"Rate: {overall['detection_rate']*100:.1f}%")
                  
        # Aggregated results
        print("\n")
        aggregated = self.aggregate_experiments(experiments)
        self.print_aggregated(aggregated)


def main():
    parser = argparse.ArgumentParser(
        description="Unified Analysis Tool for DDoS Experiments",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Summary of latest experiment
  python analyze.py summary
  
  # Summary of specific experiment
  python analyze.py summary ../results/experiments/continuous_20251125/results.json
  
  # Aggregate multiple experiments
  python analyze.py aggregate exp1/results.json exp2/results.json
  
  # Batch analysis with glob pattern
  python analyze.py batch ../results/experiments/continuous_*/results.json
        """
    )
    
    subparsers = parser.add_subparsers(dest='mode', required=True, help='Analysis mode')
    
    # Summary mode
    summary = subparsers.add_parser('summary', help='Quick summary of single experiment')
    summary.add_argument('file', nargs='?', help='Path to results.json (auto-detect if not provided)')
    
    # Aggregate mode
    aggregate = subparsers.add_parser('aggregate', help='Aggregate multiple experiments')
    aggregate.add_argument('files', nargs='+', help='Paths to results.json files')
    
    # Batch mode
    batch = subparsers.add_parser('batch', help='Batch analysis with comparisons')
    batch.add_argument('pattern', help='Glob pattern for experiment directories')
    
    args = parser.parse_args()
    analyzer = ExperimentAnalyzer()
    
    if args.mode == 'summary':
        if args.file:
            json_file = args.file
        else:
            try:
                json_file = analyzer.find_latest_experiment()
                print(f"Using most recent: {json_file.parent.name}\n")
            except FileNotFoundError as e:
                print(f"Error: {e}")
                sys.exit(1)
                
        data = analyzer.load_experiment(json_file)
        analyzer.print_summary(data)
        
    elif args.mode == 'aggregate':
        experiments = [analyzer.load_experiment(f) for f in args.files]
        aggregated = analyzer.aggregate_experiments(experiments)
        analyzer.print_aggregated(aggregated)
        
    elif args.mode == 'batch':
        # Expand glob pattern
        if '*' in args.pattern:
            files = glob.glob(args.pattern)
        else:
            # Treat as directory pattern
            pattern = Path(args.pattern)
            if pattern.is_dir():
                files = list(pattern.glob('*/results.json'))
            else:
                files = glob.glob(str(pattern))
                
        if not files:
            print(f"No files found matching: {args.pattern}")
            sys.exit(1)
            
        analyzer.batch_analysis(files)


if __name__ == '__main__':
    main()
