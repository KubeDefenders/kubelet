#!/usr/bin/env python3
"""
Unified DDoS Experiment Runner

Runs DDoS detection experiments with flexible modes:
- continuous: Random attack injection with continuous monitoring
- research: Structured research data collection
- single: Single attack test

Usage:
    # 15-minute continuous experiment
    python experiment.py continuous --target-url http://192.168.49.2:30001 --duration 900
    
    # Research mode
    python experiment.py research --target-url http://192.168.49.2:30001
    
    # Single attack test
    python experiment.py single --target-url http://192.168.49.2:30001 --attack-type http-flood --duration 120
"""

import asyncio
import subprocess
import time
import signal
import sys
import argparse
import json
import random
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from dataclasses import dataclass, asdict


@dataclass
class AttackResult:
    """Record of single attack execution and detection"""
    attack_id: str
    attack_type: str
    start_time: str
    end_time: str
    duration_seconds: int
    workers: int
    rate_per_worker: int
    total_rate: int
    detected: Optional[bool] = None
    detection_time: Optional[str] = None
    detection_latency_sec: Optional[float] = None
    false_negative: bool = False
    notes: str = ""


class ExperimentRunner:
    """Unified experiment runner for all modes"""
    
    # Attack type configurations
    ATTACK_CONFIGS = {
        'http-flood': {
            'workers': [20, 25, 30, 35, 40],
            'rate': [8, 10, 12, 15],
            'duration': [30, 60, 90, 120, 150, 180],
            'weight': 3
        },
        'slowloris': {
            'workers': [15, 20, 25, 30],
            'rate': [8, 10, 12, 15],
            'duration': [60, 90, 120, 150, 180],
            'weight': 2
        },
        'syn': {
            'workers': [20, 25, 30, 35],
            'rate': [8, 10, 12],
            'duration': [30, 60, 90, 120, 150, 180],
            'weight': 2
        },
        'udp': {
            'workers': [25, 30, 35, 40],
            'rate': [8, 10, 12],
            'duration': [30, 60, 90, 120, 150],
            'weight': 1
        },
        'dns': {
            'workers': [15, 20, 25, 30],
            'rate': [10, 12, 15, 18],
            'duration': [30, 60, 90, 120, 150],
            'weight': 2
        }
    }
    
    MIN_ATTACK_DURATION = 30
    MAX_ATTACK_DURATION = 180
    ATTACK_GAP = 30
    
    def __init__(self, mode: str, target_url: str, output_dir: str = "../results/experiments"):
        self.mode = mode
        self.target_url = target_url
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.experiment_dir = self.output_dir / f"{mode}_{self.timestamp}"
        self.experiment_dir.mkdir(exist_ok=True)
        
        self.attack_results: List[AttackResult] = []
        self.processes: Dict[str, subprocess.Popen] = {}
        self.monitor_alerts: List[Dict] = []
        
    def log(self, message: str, level: str = "INFO"):
        """Log message with timestamp"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        prefix = {"INFO": "ℹ️", "ATTACK": "⚔️", "DETECT": "🚨", "WARN": "⚠️", "ERROR": "❌"}.get(level, "📝")
        print(f"[{timestamp}] {prefix}  {message}")
        
    def generate_random_attack(self) -> Dict:
        """Generate random attack configuration"""
        # Weighted random selection
        attack_types = []
        for attack_type, config in self.ATTACK_CONFIGS.items():
            attack_types.extend([attack_type] * config['weight'])
        
        attack_type = random.choice(attack_types)
        config = self.ATTACK_CONFIGS[attack_type]
        
        return {
            'type': attack_type,
            'workers': random.choice(config['workers']),
            'rate': random.choice(config['rate']),
            'duration': random.choice(config['duration'])
        }
    
    async def execute_attack(self, attack_config: Dict) -> AttackResult:
        """Execute single attack and record results"""
        attack_type = attack_config['type']
        workers = attack_config['workers']
        rate = attack_config['rate']
        duration = attack_config['duration']
        total_rate = workers * rate
        
        attack_id = f"attack_{len(self.attack_results) + 1:03d}"
        
        self.log(f"Attack {len(self.attack_results) + 1}: {attack_type.upper()}", "ATTACK")
        self.log(f"  Config: {workers} workers × {rate} req/s = {total_rate} req/s for {duration}s", "INFO")
        
        start_time = datetime.now()
        
        command = [
            sys.executable,
            "../attack-simulations/attack.py",
            "--target-url", self.target_url,
            "--attack-type", attack_type,
            "--workers", str(workers),
            "--duration", str(duration),
            "--rate", str(rate)
        ]
        
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=duration + 30
            )
            end_time = datetime.now()
            actual_duration = int((end_time - start_time).total_seconds())
            
            attack_result = AttackResult(
                attack_id=attack_id,
                attack_type=attack_type,
                start_time=start_time.isoformat(),
                end_time=end_time.isoformat(),
                duration_seconds=actual_duration,
                workers=workers,
                rate_per_worker=rate,
                total_rate=total_rate,
                notes="completed"
            )
            
            self.attack_results.append(attack_result)
            self.log(f"  Completed in {actual_duration}s", "INFO")
            return attack_result
            
        except subprocess.TimeoutExpired:
            end_time = datetime.now()
            attack_result = AttackResult(
                attack_id=attack_id,
                attack_type=attack_type,
                start_time=start_time.isoformat(),
                end_time=end_time.isoformat(),
                duration_seconds=duration,
                workers=workers,
                rate_per_worker=rate,
                total_rate=total_rate,
                notes="timeout"
            )
            self.attack_results.append(attack_result)
            self.log(f"  Timeout after {duration}s", "WARN")
            return attack_result
            
    async def start_ml_monitor(self):
        """Start ML detection monitor"""
        self.log("Starting ML monitor...", "INFO")
        monitor_log = self.experiment_dir / "monitor.log"
        
        command = [
            sys.executable,
            "../ml-optimized-detector/continuous_monitor.py",
            "--model", "../ml-optimized-detector/models/practical_detector.pkl",
            "--threshold", "2",
            "--interval", "5"
        ]
        
        log_file = open(monitor_log, "w")
        process = subprocess.Popen(
            command,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True
        )
        
        self.processes['monitor'] = process
        self.log(f"ML monitor started (PID: {process.pid})", "INFO")
        await asyncio.sleep(2)
        
    async def start_normal_traffic(self):
        """Start normal traffic baseline"""
        self.log("Starting normal traffic...", "INFO")
        traffic_log = self.experiment_dir / "traffic.log"
        
        command = [
            sys.executable,
            "traffic-generator.py",
            "--target-url", self.target_url,
            "--workers", "5",
            "--rate", "3"
        ]
        
        log_file = open(traffic_log, "w")
        process = subprocess.Popen(
            command,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True
        )
        
        self.processes['traffic'] = process
        self.log(f"Normal traffic started (PID: {process.pid}, 15 req/s)", "INFO")
        await asyncio.sleep(2)
        
    def load_monitor_alerts(self) -> List[Dict]:
        """Load alerts from ML monitor log"""
        monitor_log = self.experiment_dir / "monitor.log"
        if not monitor_log.exists():
            return []
            
        alerts = []
        with open(monitor_log) as f:
            for line in f:
                if "ALERT" in line or "ATTACK" in line:
                    try:
                        timestamp_str = line.split()[0] + " " + line.split()[1]
                        alert_time = datetime.fromisoformat(timestamp_str)
                        alerts.append({
                            'time': alert_time.isoformat(),
                            'message': line.strip()
                        })
                    except:
                        pass
        return alerts
        
    def correlate_attacks_with_detections(self):
        """Match attacks with ML detections"""
        alerts = self.load_monitor_alerts()
        
        for attack in self.attack_results:
            attack_start = datetime.fromisoformat(attack.start_time)
            attack_end = datetime.fromisoformat(attack.end_time)
            detection_window_end = attack_end + timedelta(seconds=30)
            
            for alert in alerts:
                alert_time = datetime.fromisoformat(alert['time'])
                if attack_start <= alert_time <= detection_window_end:
                    attack.detected = True
                    attack.detection_time = alert['time']
                    attack.detection_latency_sec = (alert_time - attack_start).total_seconds()
                    break
            
            if attack.detected is None:
                attack.detected = False
                attack.false_negative = True
                
    def calculate_statistics(self) -> Dict:
        """Calculate comprehensive statistics"""
        total_attacks = len(self.attack_results)
        detected = sum(1 for a in self.attack_results if a.detected)
        missed = total_attacks - detected
        
        stats = {
            'overall': {
                'total_attacks': total_attacks,
                'detected': detected,
                'missed': missed,
                'detection_rate': detected / total_attacks if total_attacks > 0 else 0
            },
            'by_attack_type': {}
        }
        
        # Per-attack-type statistics
        attack_types = set(a.attack_type for a in self.attack_results)
        for attack_type in attack_types:
            type_attacks = [a for a in self.attack_results if a.attack_type == attack_type]
            type_detected = [a for a in type_attacks if a.detected]
            
            latencies = [a.detection_latency_sec for a in type_detected if a.detection_latency_sec]
            
            stats['by_attack_type'][attack_type] = {
                'total': len(type_attacks),
                'detected': len(type_detected),
                'missed': len(type_attacks) - len(type_detected),
                'detection_rate': len(type_detected) / len(type_attacks) if type_attacks else 0,
                'mean_latency': sum(latencies) / len(latencies) if latencies else 0,
                'min_latency': min(latencies) if latencies else 0,
                'max_latency': max(latencies) if latencies else 0
            }
        
        # Detection latencies
        all_latencies = [a.detection_latency_sec for a in self.attack_results if a.detection_latency_sec]
        stats['detection_latencies'] = all_latencies
        
        return stats
        
    def save_results(self):
        """Save experiment results"""
        results = {
            'metadata': {
                'mode': self.mode,
                'experiment_start': self.attack_results[0].start_time if self.attack_results else None,
                'experiment_end': self.attack_results[-1].end_time if self.attack_results else None,
                'target_url': self.target_url
            },
            'attacks': [asdict(a) for a in self.attack_results],
            'statistics': self.calculate_statistics()
        }
        
        # Save JSON
        json_file = self.experiment_dir / "results.json"
        with open(json_file, 'w') as f:
            json.dump(results, f, indent=2)
            
        self.log(f"Results saved to {json_file}", "INFO")
        
        # Print statistics
        self.print_statistics(results['statistics'])
        
    def print_statistics(self, stats: Dict):
        """Print statistics to console"""
        print("\n" + "="*70)
        print("📊 EXPERIMENT STATISTICS")
        print("="*70)
        
        overall = stats['overall']
        print(f"\nOverall Performance:")
        print(f"  Total Attacks:   {overall['total_attacks']}")
        print(f"  Detected:        {overall['detected']} ({overall['detection_rate']*100:.1f}%)")
        print(f"  Missed:          {overall['missed']} ({(1-overall['detection_rate'])*100:.1f}%)")
        
        print(f"\nPer Attack Type:")
        print(f"  {'Type':<15} {'Total':>6} {'Detected':>9} {'Missed':>7} {'Rate':>7} {'Avg Latency':>12}")
        print("  " + "-"*66)
        for attack_type, type_stats in stats['by_attack_type'].items():
            latency = f"{type_stats['mean_latency']:.1f}s" if type_stats['mean_latency'] > 0 else "N/A"
            print(f"  {attack_type:<15} {type_stats['total']:>6} {type_stats['detected']:>9} "
                  f"{type_stats['missed']:>7} {type_stats['detection_rate']*100:>6.1f}% {latency:>12}")
        
        print("="*70 + "\n")
        
    async def run_continuous_mode(self, duration: int):
        """Run continuous monitoring experiment"""
        self.log(f"Starting CONTINUOUS experiment ({duration}s)", "INFO")
        
        await self.start_ml_monitor()
        await self.start_normal_traffic()
        
        self.log("Establishing baseline (30s)...", "INFO")
        await asyncio.sleep(30)
        
        end_time = datetime.now() + timedelta(seconds=duration)
        
        while datetime.now() < end_time:
            remaining = (end_time - datetime.now()).total_seconds()
            
            if remaining < 60:
                break
                
            attack_config = self.generate_random_attack()
            attack_duration = attack_config['duration']
            
            if remaining < attack_duration + 90:
                break
                
            await self.execute_attack(attack_config)
            
            recovery = random.randint(30, 60)
            self.log(f"Recovery period ({recovery}s)...", "INFO")
            await asyncio.sleep(recovery)
            
        self.log("Final observation period (30s)...", "INFO")
        await asyncio.sleep(30)
        
        self.correlate_attacks_with_detections()
        self.save_results()
        
    async def run_research_mode(self):
        """Run structured research experiment"""
        self.log("Starting RESEARCH experiment", "INFO")
        
        await self.start_ml_monitor()
        await self.start_normal_traffic()
        
        self.log("Establishing baseline (30s)...", "INFO")
        await asyncio.sleep(30)
        
        # Structured attack sequence for research
        attack_sequence = [
            {'type': 'http-flood', 'workers': 30, 'rate': 10, 'duration': 120},
            {'type': 'slowloris', 'workers': 25, 'rate': 10, 'duration': 120},
            {'type': 'syn', 'workers': 30, 'rate': 10, 'duration': 120},
            {'type': 'udp', 'workers': 35, 'rate': 10, 'duration': 120},
            {'type': 'dns', 'workers': 25, 'rate': 12, 'duration': 120}
        ]
        
        for attack_config in attack_sequence:
            await self.execute_attack(attack_config)
            self.log("Recovery period (60s)...", "INFO")
            await asyncio.sleep(60)
            
        self.log("Final observation period (60s)...", "INFO")
        await asyncio.sleep(60)
        
        self.correlate_attacks_with_detections()
        self.save_results()
        
    async def run_single_attack(self, attack_type: str, duration: int, workers: int = None, rate: int = None):
        """Run single attack test"""
        self.log(f"Starting SINGLE attack test: {attack_type}", "INFO")
        
        await self.start_ml_monitor()
        await self.start_normal_traffic()
        
        self.log("Establishing baseline (20s)...", "INFO")
        await asyncio.sleep(20)
        
        config = self.ATTACK_CONFIGS.get(attack_type)
        if not config:
            raise ValueError(f"Unknown attack type: {attack_type}")
            
        attack_config = {
            'type': attack_type,
            'workers': workers or random.choice(config['workers']),
            'rate': rate or random.choice(config['rate']),
            'duration': duration
        }
        
        await self.execute_attack(attack_config)
        
        self.log("Observation period (30s)...", "INFO")
        await asyncio.sleep(30)
        
        self.correlate_attacks_with_detections()
        self.save_results()
        
    def cleanup(self):
        """Stop all processes"""
        self.log("Cleaning up...", "INFO")
        for name, process in self.processes.items():
            if process.poll() is None:
                self.log(f"Stopping {name} (PID: {process.pid})", "INFO")
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    
    async def run(self, **kwargs):
        """Main entry point"""
        try:
            if self.mode == 'continuous':
                await self.run_continuous_mode(kwargs['duration'])
            elif self.mode == 'research':
                await self.run_research_mode()
            elif self.mode == 'single':
                await self.run_single_attack(
                    kwargs['attack_type'],
                    kwargs['duration'],
                    kwargs.get('workers'),
                    kwargs.get('rate')
                )
            else:
                raise ValueError(f"Unknown mode: {self.mode}")
        finally:
            self.cleanup()


async def main():
    parser = argparse.ArgumentParser(
        description="Unified DDoS Experiment Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # 15-minute continuous experiment
  python experiment.py continuous --target-url http://192.168.49.2:30001 --duration 900
  
  # Research mode
  python experiment.py research --target-url http://192.168.49.2:30001
  
  # Single attack test
  python experiment.py single --target-url http://192.168.49.2:30001 \\
      --attack-type http-flood --duration 120 --workers 30 --rate 10
        """
    )
    
    subparsers = parser.add_subparsers(dest='mode', required=True, help='Experiment mode')
    
    # Continuous mode
    continuous = subparsers.add_parser('continuous', help='Continuous monitoring with random attacks')
    continuous.add_argument('--target-url', required=True, help='Target URL')
    continuous.add_argument('--duration', type=int, default=900, help='Duration in seconds (default: 900 = 15 min)')
    continuous.add_argument('--output-dir', default='../results/experiments', help='Output directory')
    
    # Research mode
    research = subparsers.add_parser('research', help='Structured research experiment')
    research.add_argument('--target-url', required=True, help='Target URL')
    research.add_argument('--output-dir', default='../results/experiments', help='Output directory')
    
    # Single attack mode
    single = subparsers.add_parser('single', help='Single attack test')
    single.add_argument('--target-url', required=True, help='Target URL')
    single.add_argument('--attack-type', required=True, 
                       choices=['http-flood', 'slowloris', 'syn', 'udp', 'dns'],
                       help='Attack type')
    single.add_argument('--duration', type=int, required=True, help='Attack duration in seconds')
    single.add_argument('--workers', type=int, help='Number of workers (optional)')
    single.add_argument('--rate', type=int, help='Rate per worker (optional)')
    single.add_argument('--output-dir', default='../results/experiments', help='Output directory')
    
    args = parser.parse_args()
    
    print(f"\n{'='*70}")
    print(f"🧪 DDoS Experiment Runner - {args.mode.upper()} Mode")
    print(f"{'='*70}\n")
    print(f"📍 Target URL: {args.target_url}")
    print(f"📁 Output: {args.output_dir}\n")
    
    runner = ExperimentRunner(
        mode=args.mode,
        target_url=args.target_url,
        output_dir=args.output_dir
    )
    
    await runner.run(**vars(args))


if __name__ == "__main__":
    asyncio.run(main())
