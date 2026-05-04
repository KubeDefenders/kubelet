#!/usr/bin/env python3
"""
Generate comparison table for DDoS mitigation experiment results
Analyzes metrics from baseline, native, and nephio scenarios
"""

import json
import sys
import os
import re
from pathlib import Path
from typing import Dict, List, Tuple


def load_metrics(results_dir: str) -> Dict[str, Dict[str, Dict]]:
    """Load all metrics files from experiment directory"""
    metrics = {
        'baseline': {},
        'native': {},
        'nephio': {}
    }
    
    results_path = Path(results_dir)
    
    # Load metrics for each scenario
    for scenario in ['baseline', 'native-mitigations', 'nephio-mitigations']:
        scenario_key = scenario.split('-')[0]
        
        for phase in ['pre', 'during', 'post']:
            filename = f"metrics-{phase}-{scenario}.json"
            filepath = results_path / filename
            
            if filepath.exists():
                with open(filepath) as f:
                    metrics[scenario_key][phase] = json.load(f)
    
    return metrics


def load_attack_logs(results_dir: str) -> Dict[str, Dict]:
    """Extract attack statistics from log files"""
    attack_stats = {}
    results_path = Path(results_dir)
    
    for scenario in ['baseline', 'native-mitigations', 'nephio-mitigations']:
        scenario_key = scenario.split('-')[0]
        logfile = results_path / f"attack-{scenario}.log"
        
        if logfile.exists():
            with open(logfile) as f:
                content = f.read()
                
                # Extract key metrics using regex
                stats = {}
                
                # Total requests
                match = re.search(r'Total Requests:\s*([\d,]+)', content)
                if match:
                    stats['total_requests'] = int(match.group(1).replace(',', ''))
                
                # Success rate
                match = re.search(r'Successful:\s*[\d,]+\s*\(([\d.]+)%\)', content)
                if match:
                    stats['success_rate'] = float(match.group(1))
                
                # Failure rate
                match = re.search(r'Failed:\s*[\d,]+\s*\(([\d.]+)%\)', content)
                if match:
                    stats['failure_rate'] = float(match.group(1))
                
                # Actual rate
                match = re.search(r'Actual Rate:\s*([\d.]+)\s*req/s', content)
                if match:
                    stats['actual_rate'] = float(match.group(1))
                
                # Average latency
                match = re.search(r'Avg Latency:\s*([\d.]+)ms', content)
                if match:
                    stats['avg_latency'] = float(match.group(1))
                
                # HTTP 404 errors
                match = re.search(r'404:\s*([\d,]+)', content)
                if match:
                    stats['http_404'] = int(match.group(1).replace(',', ''))
                
                attack_stats[scenario_key] = stats
    
    return attack_stats


def calculate_improvements(baseline: Dict, comparison: Dict) -> Dict:
    """Calculate percentage improvements"""
    improvements = {}
    
    for key in baseline:
        if key in comparison and baseline[key] != 0:
            if key in ['failure_rate', 'avg_latency', 'http_404']:
                # Lower is better
                improvements[key] = ((baseline[key] - comparison[key]) / baseline[key]) * 100
            else:
                # Higher is better
                improvements[key] = ((comparison[key] - baseline[key]) / baseline[key]) * 100
    
    return improvements


def format_value(key: str, value: float) -> str:
    """Format value with appropriate units and precision"""
    if value == 0:
        return "0"
    
    if 'rate' in key and 'success' not in key and 'failure' not in key:
        return f"{value:,.1f} req/s"
    elif key in ['success_rate', 'failure_rate']:
        return f"{value:.1f}%"
    elif key == 'avg_latency':
        return f"{value:.1f}ms"
    elif key in ['total_requests', 'http_404']:
        return f"{int(value):,}"
    elif key in ['pods', 'hpa_count', 'network_policies', 'resource_quotas', 'nephio_managed_resources']:
        return f"{int(value)}"
    elif key in ['cpu_millicores']:
        return f"{int(value)}m"
    elif key in ['memory_mb']:
        return f"{int(value)}Mi"
    else:
        return f"{value:.2f}"


def print_comparison_table(metrics: Dict, attack_stats: Dict):
    """Print comprehensive comparison table"""
    
    print("\n" + "="*100)
    print("DDOS MITIGATION EFFECTIVENESS - COMPREHENSIVE COMPARISON")
    print("="*100)
    
    # Attack Performance Comparison
    print("\n╔" + "═"*98 + "╗")
    print("║" + " ATTACK PERFORMANCE METRICS".center(98) + "║")
    print("╠" + "═"*98 + "╣")
    print("║ Metric                    │ Baseline          │ Native K8s        │ Nephio            │ Improvement    ║")
    print("╠" + "═"*98 + "╣")
    
    metrics_order = [
        ('total_requests', 'Total Requests'),
        ('actual_rate', 'Actual Attack Rate'),
        ('success_rate', 'Success Rate'),
        ('failure_rate', 'Failure Rate'),
        ('avg_latency', 'Average Latency'),
        ('http_404', 'HTTP 404 Errors'),
    ]
    
    for key, label in metrics_order:
        baseline_val = attack_stats.get('baseline', {}).get(key, 0)
        native_val = attack_stats.get('native', {}).get(key, 0)
        nephio_val = attack_stats.get('nephio', {}).get(key, 0)
        
        # Calculate improvement (native vs baseline, nephio vs baseline)
        if baseline_val != 0:
            if key in ['failure_rate', 'avg_latency', 'http_404']:
                native_imp = ((baseline_val - native_val) / baseline_val) * 100
                nephio_imp = ((baseline_val - nephio_val) / baseline_val) * 100
            else:
                native_imp = ((native_val - baseline_val) / baseline_val) * 100
                nephio_imp = ((nephio_val - baseline_val) / baseline_val) * 100
            
            improvement = f"N:{native_imp:+.1f}% │ P:{nephio_imp:+.1f}%"
        else:
            improvement = "N/A"
        
        print(f"║ {label:<25} │ {format_value(key, baseline_val):>17} │ "
              f"{format_value(key, native_val):>17} │ {format_value(key, nephio_val):>17} │ "
              f"{improvement:>14} ║")
    
    print("╚" + "═"*98 + "╝")
    
    # System Resource Metrics
    print("\n╔" + "═"*98 + "╗")
    print("║" + " SYSTEM RESOURCE METRICS (During Attack)".center(98) + "║")
    print("╠" + "═"*98 + "╣")
    print("║ Metric                    │ Baseline          │ Native K8s        │ Nephio            │ Change         ║")
    print("╠" + "═"*98 + "╣")
    
    resource_metrics = [
        ('pods', 'Pod Count'),
        ('cpu_millicores', 'CPU Usage'),
        ('memory_mb', 'Memory Usage'),
    ]
    
    for key, label in resource_metrics:
        baseline_val = metrics.get('baseline', {}).get('during', {}).get(key, 0)
        native_val = metrics.get('native', {}).get('during', {}).get(key, 0)
        nephio_val = metrics.get('nephio', {}).get('during', {}).get(key, 0)
        
        baseline_pre = metrics.get('baseline', {}).get('pre', {}).get(key, 0)
        native_pre = metrics.get('native', {}).get('pre', {}).get(key, 0)
        nephio_pre = metrics.get('nephio', {}).get('pre', {}).get(key, 0)
        
        baseline_change = baseline_val - baseline_pre
        native_change = native_val - native_pre
        nephio_change = nephio_val - nephio_pre
        
        change_str = f"B:{baseline_change:+d} N:{native_change:+d} P:{nephio_change:+d}"
        
        print(f"║ {label:<25} │ {format_value(key, baseline_val):>17} │ "
              f"{format_value(key, native_val):>17} │ {format_value(key, nephio_val):>17} │ "
              f"{change_str:>14} ║")
    
    print("╚" + "═"*98 + "╝")
    
    # Mitigation Infrastructure
    print("\n╔" + "═"*98 + "╗")
    print("║" + " MITIGATION INFRASTRUCTURE DEPLOYED".center(98) + "║")
    print("╠" + "═"*98 + "╣")
    print("║ Protection Layer          │ Baseline          │ Native K8s        │ Nephio            │ Delta          ║")
    print("╠" + "═"*98 + "╣")
    
    mitigation_metrics = [
        ('hpa_count', 'Horizontal Pod Autoscalers'),
        ('network_policies', 'Network Policies'),
        ('resource_quotas', 'Resource Quotas'),
        ('nephio_managed_resources', 'Nephio Managed Resources'),
    ]
    
    for key, label in mitigation_metrics:
        baseline_val = metrics.get('baseline', {}).get('during', {}).get(key, 0)
        native_val = metrics.get('native', {}).get('during', {}).get(key, 0)
        nephio_val = metrics.get('nephio', {}).get('during', {}).get(key, 0)
        
        native_delta = native_val - baseline_val
        nephio_delta = nephio_val - baseline_val
        
        delta_str = f"+{native_delta} / +{nephio_delta}"
        
        print(f"║ {label:<25} │ {format_value(key, baseline_val):>17} │ "
              f"{format_value(key, native_val):>17} │ {format_value(key, nephio_val):>17} │ "
              f"{delta_str:>14} ║")
    
    print("╚" + "═"*98 + "╝")
    
    # Summary and Recommendations
    print("\n╔" + "═"*98 + "╗")
    print("║" + " EFFECTIVENESS SUMMARY".center(98) + "║")
    print("╠" + "═"*98 + "╣")
    
    # Calculate overall effectiveness scores
    baseline_stats = attack_stats.get('baseline', {})
    native_stats = attack_stats.get('native', {})
    nephio_stats = attack_stats.get('nephio', {})
    
    if baseline_stats.get('failure_rate', 0) != 0:
        native_reduction = ((baseline_stats.get('failure_rate', 0) - native_stats.get('failure_rate', 0)) 
                           / baseline_stats.get('failure_rate', 0)) * 100
        nephio_reduction = ((baseline_stats.get('failure_rate', 0) - nephio_stats.get('failure_rate', 0)) 
                           / baseline_stats.get('failure_rate', 0)) * 100
    else:
        native_reduction = 0
        nephio_reduction = 0
    
    print(f"║                                                                                                  ║")
    print(f"║ Native Kubernetes Mitigations:  {native_reduction:>6.1f}% reduction in failure rate                              ║")
    print(f"║ Nephio Enhanced Mitigations:    {nephio_reduction:>6.1f}% reduction in failure rate                              ║")
    print(f"║                                                                                                  ║")
    
    # Determine winner
    if nephio_reduction > native_reduction:
        advantage = nephio_reduction - native_reduction
        print(f"║ 🏆 WINNER: Nephio provides {advantage:>5.1f}% better protection than Native K8s                               ║")
    elif native_reduction > nephio_reduction:
        advantage = native_reduction - nephio_reduction
        print(f"║ 🏆 WINNER: Native K8s provides {advantage:>5.1f}% better protection than Nephio                             ║")
    else:
        print(f"║ 🤝 RESULT: Both mitigations provide equivalent protection                                       ║")
    
    print(f"║                                                                                                  ║")
    print("╚" + "═"*98 + "╝")
    
    print("\nLegend:")
    print("  B = Baseline | N = Native Kubernetes | P = Nephio (Porch)")
    print("  Positive % = Improvement | Negative % = Degradation")
    print("="*100 + "\n")


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 generate-comparison-table.py <results_directory>")
        print("\nExample:")
        print("  python3 generate-comparison-table.py results/experiments/mitigation-comparison-20260101-193921")
        sys.exit(1)
    
    results_dir = sys.argv[1]
    
    if not os.path.exists(results_dir):
        print(f"Error: Directory not found: {results_dir}")
        sys.exit(1)
    
    print(f"\nAnalyzing results from: {results_dir}")
    
    # Load data
    metrics = load_metrics(results_dir)
    attack_stats = load_attack_logs(results_dir)
    
    # Generate comparison table
    print_comparison_table(metrics, attack_stats)
    
    # Save to file
    output_file = os.path.join(results_dir, "comparison-table.txt")
    
    # Redirect stdout to file
    original_stdout = sys.stdout
    with open(output_file, 'w') as f:
        sys.stdout = f
        print_comparison_table(metrics, attack_stats)
    sys.stdout = original_stdout
    
    print(f"Comparison table saved to: {output_file}")


if __name__ == "__main__":
    main()
