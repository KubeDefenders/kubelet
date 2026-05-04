#!/usr/bin/env python3
"""
Unified Statistics Tool for DDoS Research

Generates publication-ready statistics and visualizations.

Modes:
- compute: Compute statistics from experiment results
- compare: Compare multiple experiments
- report: Generate comprehensive report

Usage:
    # Compute stats for single experiment
    python statistics.py compute results.json
    
    # Compare multiple experiments
    python statistics.py compare exp1/results.json exp2/results.json exp3/results.json
    
    # Generate comprehensive report
    python statistics.py report --output report.md ../results/experiments/*
"""

import json
import sys
import argparse
from pathlib import Path
from typing import List, Dict
import statistics as stats_lib


class StatisticsGenerator:
    """Generate comprehensive statistics for research"""
    
    def __init__(self):
        self.experiments = []
        
    def load_experiment(self, path: str) -> Dict:
        """Load experiment results"""
        with open(path) as f:
            return json.load(f)
            
    def compute_statistics(self, experiment: Dict) -> Dict:
        """Compute comprehensive statistics"""
        attacks = experiment['attacks']
        detected_attacks = [a for a in attacks if a['detected']]
        
        # Detection metrics
        detection_rate = len(detected_attacks) / len(attacks) if attacks else 0
        false_negative_rate = 1 - detection_rate
        
        # Latency statistics
        latencies = [a['detection_latency_sec'] for a in detected_attacks if a['detection_latency_sec']]
        
        latency_stats = {}
        if latencies:
            latency_stats = {
                'mean': stats_lib.mean(latencies),
                'median': stats_lib.median(latencies),
                'stdev': stats_lib.stdev(latencies) if len(latencies) > 1 else 0,
                'min': min(latencies),
                'max': max(latencies),
                'q1': stats_lib.quantiles(latencies, n=4)[0] if len(latencies) >= 4 else min(latencies),
                'q3': stats_lib.quantiles(latencies, n=4)[2] if len(latencies) >= 4 else max(latencies)
            }
            
        # Per-attack-type metrics
        attack_types = set(a['attack_type'] for a in attacks)
        type_metrics = {}
        
        for attack_type in attack_types:
            type_attacks = [a for a in attacks if a['attack_type'] == attack_type]
            type_detected = [a for a in type_attacks if a['detected']]
            type_latencies = [a['detection_latency_sec'] for a in type_detected if a['detection_latency_sec']]
            
            type_metrics[attack_type] = {
                'total': len(type_attacks),
                'detected': len(type_detected),
                'missed': len(type_attacks) - len(type_detected),
                'detection_rate': len(type_detected) / len(type_attacks) if type_attacks else 0,
                'mean_latency': stats_lib.mean(type_latencies) if type_latencies else None,
                'stdev_latency': stats_lib.stdev(type_latencies) if len(type_latencies) > 1 else None
            }
            
        return {
            'overall': {
                'total_attacks': len(attacks),
                'detected': len(detected_attacks),
                'missed': len(attacks) - len(detected_attacks),
                'detection_rate': detection_rate,
                'false_negative_rate': false_negative_rate
            },
            'latency': latency_stats,
            'by_attack_type': type_metrics
        }
        
    def print_statistics(self, stats: Dict):
        """Print statistics in formatted output"""
        print("\n" + "="*80)
        print("COMPREHENSIVE STATISTICS")
        print("="*80)
        
        # Overall metrics
        overall = stats['overall']
        print(f"\nOverall Detection Performance:")
        print(f"  Total Attacks:         {overall['total_attacks']}")
        print(f"  Detected:              {overall['detected']} ({overall['detection_rate']*100:.2f}%)")
        print(f"  Missed:                {overall['missed']} ({overall['false_negative_rate']*100:.2f}%)")
        
        # Latency metrics
        if stats['latency']:
            lat = stats['latency']
            print(f"\nDetection Latency Statistics:")
            print(f"  Mean:                  {lat['mean']:.2f}s")
            print(f"  Median:                {lat['median']:.2f}s")
            print(f"  Std Dev:               {lat['stdev']:.2f}s")
            print(f"  Min:                   {lat['min']:.2f}s")
            print(f"  Max:                   {lat['max']:.2f}s")
            print(f"  Q1 (25th percentile):  {lat['q1']:.2f}s")
            print(f"  Q3 (75th percentile):  {lat['q3']:.2f}s")
            print(f"  IQR:                   {lat['q3'] - lat['q1']:.2f}s")
            
        # Per-attack-type metrics
        print(f"\nPer Attack Type Performance:")
        print(f"  {'Type':<15} {'Total':>6} {'Detected':>9} {'Rate':>7} {'Mean Lat':>10} {'StdDev':>10}")
        print("  " + "-"*66)
        for attack_type, metrics in stats['by_attack_type'].items():
            mean_lat = f"{metrics['mean_latency']:.2f}s" if metrics['mean_latency'] else "N/A"
            stdev = f"{metrics['stdev_latency']:.2f}s" if metrics['stdev_latency'] else "N/A"
            print(f"  {attack_type:<15} {metrics['total']:>6} {metrics['detected']:>9} "
                  f"{metrics['detection_rate']*100:>6.1f}% {mean_lat:>10} {stdev:>10}")
        
        print("="*80 + "\n")
        
    def compare_experiments(self, experiments: List[Dict]):
        """Compare multiple experiments"""
        print("\n" + "="*80)
        print(f"COMPARATIVE ANALYSIS ({len(experiments)} experiments)")
        print("="*80)
        
        # Compute stats for each
        all_stats = []
        for i, exp in enumerate(experiments, 1):
            stats = self.compute_statistics(exp)
            all_stats.append(stats)
            
            print(f"\nExperiment {i}:")
            print(f"  Detection Rate: {stats['overall']['detection_rate']*100:.2f}%")
            if stats['latency']:
                print(f"  Mean Latency:   {stats['latency']['mean']:.2f}s")
                
        # Aggregate comparison
        avg_detection_rate = stats_lib.mean([s['overall']['detection_rate'] for s in all_stats])
        all_latencies = []
        for s in all_stats:
            if s['latency']:
                all_latencies.append(s['latency']['mean'])
                
        print(f"\nAggregate Metrics:")
        print(f"  Average Detection Rate: {avg_detection_rate*100:.2f}%")
        if all_latencies:
            print(f"  Average Mean Latency:   {stats_lib.mean(all_latencies):.2f}s")
            print(f"  Latency Std Dev:        {stats_lib.stdev(all_latencies) if len(all_latencies) > 1 else 0:.2f}s")
        
        print("="*80 + "\n")
        
    def generate_report(self, experiments: List[Dict], output_file: str = None):
        """Generate comprehensive markdown report"""
        report = []
        report.append("# DDoS Detection Research Report\n")
        report.append(f"**Generated**: {Path().cwd()}\n")
        report.append(f"**Number of Experiments**: {len(experiments)}\n")
        
        # Aggregate all experiments
        all_attacks = []
        for exp in experiments:
            all_attacks.extend(exp['attacks'])
            
        # Compute overall statistics
        overall_stats = self.compute_statistics({'attacks': all_attacks})
        
        report.append("## Overall Performance\n")
        report.append(f"- Total Attacks: {overall_stats['overall']['total_attacks']}")
        report.append(f"- Detected: {overall_stats['overall']['detected']} ({overall_stats['overall']['detection_rate']*100:.2f}%)")
        report.append(f"- Missed: {overall_stats['overall']['missed']} ({overall_stats['overall']['false_negative_rate']*100:.2f}%)\n")
        
        if overall_stats['latency']:
            lat = overall_stats['latency']
            report.append("## Detection Latency\n")
            report.append(f"- Mean: {lat['mean']:.2f}s")
            report.append(f"- Median: {lat['median']:.2f}s")
            report.append(f"- Standard Deviation: {lat['stdev']:.2f}s")
            report.append(f"- Range: [{lat['min']:.2f}s, {lat['max']:.2f}s]\n")
            
        report.append("## Per Attack Type Performance\n")
        report.append("| Attack Type | Total | Detected | Detection Rate | Mean Latency |")
        report.append("|-------------|-------|----------|----------------|--------------|")
        for attack_type, metrics in overall_stats['by_attack_type'].items():
            mean_lat = f"{metrics['mean_latency']:.2f}s" if metrics['mean_latency'] else "N/A"
            report.append(f"| {attack_type} | {metrics['total']} | {metrics['detected']} | "
                         f"{metrics['detection_rate']*100:.1f}% | {mean_lat} |")
        report.append("")
        
        report_text = "\n".join(report)
        
        if output_file:
            with open(output_file, 'w') as f:
                f.write(report_text)
            print(f"Report saved to: {output_file}")
        else:
            print(report_text)


def main():
    parser = argparse.ArgumentParser(
        description="Unified Statistics Tool for DDoS Research",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Compute statistics
  python statistics.py compute results.json
  
  # Compare experiments
  python statistics.py compare exp1/results.json exp2/results.json exp3/results.json
  
  # Generate report
  python statistics.py report --output report.md ../results/experiments/*/results.json
        """
    )
    
    subparsers = parser.add_subparsers(dest='mode', required=True, help='Statistics mode')
    
    # Compute mode
    compute = subparsers.add_parser('compute', help='Compute statistics for single experiment')
    compute.add_argument('file', help='Path to results.json')
    
    # Compare mode
    compare = subparsers.add_parser('compare', help='Compare multiple experiments')
    compare.add_argument('files', nargs='+', help='Paths to results.json files')
    
    # Report mode
    report_parser = subparsers.add_parser('report', help='Generate comprehensive report')
    report_parser.add_argument('files', nargs='+', help='Paths to results.json files')
    report_parser.add_argument('--output', '-o', help='Output file (default: print to console)')
    
    args = parser.parse_args()
    generator = StatisticsGenerator()
    
    if args.mode == 'compute':
        experiment = generator.load_experiment(args.file)
        stats = generator.compute_statistics(experiment)
        generator.print_statistics(stats)
        
    elif args.mode == 'compare':
        experiments = [generator.load_experiment(f) for f in args.files]
        generator.compare_experiments(experiments)
        
    elif args.mode == 'report':
        experiments = [generator.load_experiment(f) for f in args.files]
        generator.generate_report(experiments, args.output)


if __name__ == '__main__':
    main()
