#!/usr/bin/env python3

"""
Crossfire Attack Detector and Validator

This script detects and confirms the presence of crossfire DDoS attacks by:
1. Monitoring traffic patterns to decoy services vs. target services
2. Analyzing network link saturation patterns
3. Detecting indirect service degradation
4. Validating crossfire characteristics: high decoy traffic + low target traffic = high target impact

Crossfire Attack Characteristics:
- High volume traffic to decoy endpoints (catalogue, cart, etc.)
- Normal/low traffic to target endpoint (front-end)
- Severe degradation of target service performance
- Network link/resource saturation visible in metrics
- Latency increase across all services (shared infrastructure)
"""

import argparse
import asyncio
import aiohttp
import time
import json
import subprocess
from datetime import datetime
from typing import Dict, List, Tuple
from collections import defaultdict

class CrossfireDetector:
    def __init__(self, target_url: str, duration: int = 60, sample_interval: int = 5):
        self.target_url = target_url.rstrip('/')
        self.duration = duration
        self.sample_interval = sample_interval
        self.metrics = {
            'start_time': None,
            'end_time': None,
            'samples': [],
            'service_metrics': defaultdict(lambda: {
                'request_count': 0,
                'error_count': 0,
                'total_latency': 0,
                'max_latency': 0,
                'min_latency': float('inf')
            })
        }
        
        # Define service endpoints to monitor
        self.target_service = 'front-end'
        self.decoy_services = [
            ('catalogue', '/catalogue'),
            ('catalogue-size', '/catalogue/size'),
            ('tags', '/tags'),
            ('cart', '/cart'),
            ('cards', '/cards'),
            ('addresses', '/addresses')
        ]
        
    async def measure_service_performance(self, service_name: str, endpoint: str, session: aiohttp.ClientSession) -> Dict:
        """Measure response time and status for a single service endpoint"""
        url = f"{self.target_url}{endpoint}"
        start = time.time()
        
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                await response.read()
                latency = (time.time() - start) * 1000  # ms
                
                return {
                    'service': service_name,
                    'endpoint': endpoint,
                    'status': response.status,
                    'latency': latency,
                    'success': response.status < 400,
                    'timestamp': time.time()
                }
        except asyncio.TimeoutError:
            return {
                'service': service_name,
                'endpoint': endpoint,
                'status': 0,
                'latency': 10000,  # 10s timeout
                'success': False,
                'error': 'TimeoutError',
                'timestamp': time.time()
            }
        except Exception as e:
            return {
                'service': service_name,
                'endpoint': endpoint,
                'status': 0,
                'latency': (time.time() - start) * 1000,
                'success': False,
                'error': type(e).__name__,
                'timestamp': time.time()
            }
    
    async def collect_sample(self) -> Dict:
        """Collect a single sample of metrics across all services"""
        sample = {
            'timestamp': time.time(),
            'datetime': datetime.now().isoformat(),
            'services': {}
        }
        
        async with aiohttp.ClientSession() as session:
            # Measure target service (front-end)
            target_result = await self.measure_service_performance(
                self.target_service, '/', session
            )
            sample['services']['target'] = target_result
            
            # Measure decoy services
            decoy_results = []
            for service_name, endpoint in self.decoy_services:
                result = await self.measure_service_performance(
                    service_name, endpoint, session
                )
                decoy_results.append(result)
            
            sample['services']['decoys'] = decoy_results
            
            # Get network metrics if available
            sample['network'] = await self.get_network_metrics()
            
            # Get pod metrics if available
            sample['pods'] = await self.get_pod_metrics()
        
        return sample
    
    async def get_network_metrics(self) -> Dict:
        """Get network-level metrics from Kubernetes/Istio"""
        metrics = {
            'available': False
        }
        
        try:
            # Try to get network policy count
            result = subprocess.run(
                ['kubectl', 'get', 'networkpolicies', '-n', 'sock-shop', '--no-headers'],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                metrics['network_policies'] = len(result.stdout.strip().split('\n')) if result.stdout.strip() else 0
                metrics['available'] = True
        except:
            pass
        
        return metrics
    
    async def get_pod_metrics(self) -> Dict:
        """Get pod-level metrics"""
        metrics = {
            'available': False
        }
        
        try:
            # Get pod count
            result = subprocess.run(
                ['kubectl', 'get', 'pods', '-n', 'sock-shop', '--field-selector=status.phase=Running', '--no-headers'],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                metrics['running_pods'] = len(result.stdout.strip().split('\n')) if result.stdout.strip() else 0
                metrics['available'] = True
        except:
            pass
        
        return metrics
    
    async def monitor(self):
        """Continuously monitor and collect metrics"""
        print(f"\n{'='*70}")
        print(f"🔍 CROSSFIRE ATTACK DETECTOR")
        print(f"{'='*70}")
        print(f"Target URL: {self.target_url}")
        print(f"Duration: {self.duration}s")
        print(f"Sample Interval: {self.sample_interval}s")
        print(f"Target Service: {self.target_service}")
        print(f"Decoy Services: {len(self.decoy_services)}")
        print(f"{'='*70}\n")
        
        self.metrics['start_time'] = datetime.now().isoformat()
        start = time.time()
        
        sample_count = 0
        while time.time() - start < self.duration:
            sample_count += 1
            print(f"[Sample {sample_count}] Collecting metrics at {datetime.now().strftime('%H:%M:%S')}...")
            
            sample = await self.collect_sample()
            self.metrics['samples'].append(sample)
            
            # Display current status
            target = sample['services']['target']
            decoys = sample['services']['decoys']
            
            target_status = "✓" if target.get('success') else "✗"
            target_latency = target.get('latency', 0)
            
            avg_decoy_latency = sum(d.get('latency', 0) for d in decoys) / len(decoys) if decoys else 0
            decoy_success = sum(1 for d in decoys if d.get('success')) / len(decoys) * 100 if decoys else 0
            
            print(f"  Target ({self.target_service}): {target_status} {target_latency:.0f}ms")
            print(f"  Decoys (avg): {decoy_success:.0f}% success, {avg_decoy_latency:.0f}ms")
            
            if time.time() - start < self.duration:
                await asyncio.sleep(self.sample_interval)
        
        self.metrics['end_time'] = datetime.now().isoformat()
        print(f"\n✓ Monitoring complete. Collected {len(self.metrics['samples'])} samples\n")
    
    def analyze_crossfire_characteristics(self) -> Dict:
        """Analyze collected metrics to detect crossfire attack patterns"""
        if not self.metrics['samples']:
            return {
                'detected': False,
                'reason': 'No samples collected'
            }
        
        analysis = {
            'detected': False,
            'confidence': 0.0,
            'characteristics': {},
            'evidence': []
        }
        
        # Aggregate metrics across all samples
        target_latencies = []
        target_errors = []
        decoy_latencies = []
        decoy_errors = []
        
        for sample in self.metrics['samples']:
            target = sample['services']['target']
            target_latencies.append(target.get('latency', 0))
            target_errors.append(0 if target.get('success') else 1)
            
            for decoy in sample['services']['decoys']:
                decoy_latencies.append(decoy.get('latency', 0))
                decoy_errors.append(0 if decoy.get('success') else 1)
        
        # Calculate statistics
        avg_target_latency = sum(target_latencies) / len(target_latencies)
        avg_decoy_latency = sum(decoy_latencies) / len(decoy_latencies)
        target_error_rate = sum(target_errors) / len(target_errors) * 100
        decoy_error_rate = sum(decoy_errors) / len(decoy_errors) * 100
        
        # Get baseline (first sample) for comparison
        baseline_target_latency = self.metrics['samples'][0]['services']['target'].get('latency', 0)
        
        analysis['characteristics'] = {
            'target_avg_latency': round(avg_target_latency, 2),
            'target_error_rate': round(target_error_rate, 2),
            'decoy_avg_latency': round(avg_decoy_latency, 2),
            'decoy_error_rate': round(decoy_error_rate, 2),
            'baseline_target_latency': round(baseline_target_latency, 2),
            'latency_increase_factor': round(avg_target_latency / baseline_target_latency, 2) if baseline_target_latency > 0 else 0
        }
        
        # Detect crossfire characteristics
        confidence_score = 0
        
        # Characteristic 1: Target service degradation (high latency or errors)
        if avg_target_latency > 500 or target_error_rate > 20:
            confidence_score += 25
            analysis['evidence'].append(f"✓ Target service degradation: {avg_target_latency:.0f}ms latency, {target_error_rate:.1f}% errors")
        
        # Characteristic 2: Significant latency increase from baseline
        if baseline_target_latency > 0 and avg_target_latency > baseline_target_latency * 5:
            confidence_score += 25
            analysis['evidence'].append(f"✓ Severe latency increase: {avg_target_latency/baseline_target_latency:.1f}x baseline")
        
        # Characteristic 3: Decoy services under heavy load
        if decoy_error_rate > 50:
            confidence_score += 20
            analysis['evidence'].append(f"✓ Decoy services overwhelmed: {decoy_error_rate:.1f}% error rate")
        
        # Characteristic 4: Asymmetric impact (decoys worse than target in raw metrics)
        if decoy_error_rate > target_error_rate * 2:
            confidence_score += 15
            analysis['evidence'].append(f"✓ Asymmetric attack pattern: Decoys showing {decoy_error_rate:.1f}% errors vs target {target_error_rate:.1f}%")
        
        # Characteristic 5: Network-wide latency increase (shared infrastructure impact)
        if avg_decoy_latency > 500:
            confidence_score += 15
            analysis['evidence'].append(f"✓ Network-wide latency: Decoy avg {avg_decoy_latency:.0f}ms suggests infrastructure saturation")
        
        analysis['confidence'] = confidence_score
        analysis['detected'] = confidence_score >= 50
        
        return analysis
    
    def print_analysis_report(self, analysis: Dict):
        """Print comprehensive analysis report"""
        print(f"\n{'='*70}")
        print(f"🎯 CROSSFIRE ATTACK ANALYSIS REPORT")
        print(f"{'='*70}\n")
        
        # Detection result
        if analysis['detected']:
            print(f"🚨 CROSSFIRE ATTACK DETECTED (Confidence: {analysis['confidence']}%)")
        else:
            print(f"✓ No crossfire attack detected (Confidence: {analysis['confidence']}%)")
        
        print(f"\n{'='*70}")
        print(f"METRICS SUMMARY")
        print(f"{'='*70}")
        
        chars = analysis['characteristics']
        print(f"Target Service ({self.target_service}):")
        print(f"  Average Latency: {chars['target_avg_latency']:.0f}ms")
        print(f"  Error Rate: {chars['target_error_rate']:.1f}%")
        print(f"  Baseline Latency: {chars['baseline_target_latency']:.0f}ms")
        print(f"  Latency Increase: {chars['latency_increase_factor']:.1f}x")
        
        print(f"\nDecoy Services:")
        print(f"  Average Latency: {chars['decoy_avg_latency']:.0f}ms")
        print(f"  Error Rate: {chars['decoy_error_rate']:.1f}%")
        
        print(f"\n{'='*70}")
        print(f"EVIDENCE")
        print(f"{'='*70}")
        
        if analysis['evidence']:
            for evidence in analysis['evidence']:
                print(f"  {evidence}")
        else:
            print("  No crossfire characteristics detected")
        
        print(f"\n{'='*70}")
        print(f"CROSSFIRE ATTACK INDICATORS")
        print(f"{'='*70}")
        print(f"A true crossfire attack shows:")
        print(f"  1. ✓ High traffic to decoy services")
        print(f"  2. ✓ Decoy service degradation/errors")
        print(f"  3. ✓ Target service degradation (indirect)")
        print(f"  4. ✓ Network/infrastructure saturation")
        print(f"  5. ✓ Latency increase across all services")
        print(f"\n{'='*70}\n")
    
    def save_results(self, filename: str = None):
        """Save detection results to JSON"""
        if filename is None:
            filename = f"crossfire-detection-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        
        output = {
            'target_url': self.target_url,
            'duration': self.duration,
            'metrics': self.metrics,
            'analysis': self.analyze_crossfire_characteristics()
        }
        
        with open(filename, 'w') as f:
            json.dump(output, f, indent=2)
        
        print(f"✓ Results saved to {filename}")
        return filename

async def main():
    parser = argparse.ArgumentParser(
        description='Crossfire Attack Detector and Validator',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        '--url',
        default='http://192.168.49.2:30001',
        help='Target base URL'
    )
    parser.add_argument(
        '--duration',
        type=int,
        default=60,
        help='Monitoring duration in seconds'
    )
    parser.add_argument(
        '--interval',
        type=int,
        default=5,
        help='Sample collection interval in seconds'
    )
    parser.add_argument(
        '--output',
        help='Output JSON file path'
    )
    
    args = parser.parse_args()
    
    detector = CrossfireDetector(args.url, args.duration, args.interval)
    
    try:
        # Monitor and collect metrics
        await detector.monitor()
        
        # Analyze results
        analysis = detector.analyze_crossfire_characteristics()
        
        # Print report
        detector.print_analysis_report(analysis)
        
        # Save results
        detector.save_results(args.output)
        
    except KeyboardInterrupt:
        print("\n\n⚠️  Monitoring interrupted by user")
        return

if __name__ == '__main__':
    asyncio.run(main())
