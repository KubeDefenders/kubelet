#!/usr/bin/env python3
"""
Comprehensive Crossfire Attack Testing Framework
Tests 3 scenarios:
1. Baseline attack (no mitigations)
2. Native Kubernetes mitigations
3. Nephio-enhanced mitigations

Provides dynamic, real-time, presentable output
"""

import argparse
import asyncio
import aiohttp
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import os

# ANSI color codes
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    END = '\033[0m'

def colored(text: str, color: str) -> str:
    """Return colored text"""
    return f"{color}{text}{Colors.END}"

def print_header(text: str):
    """Print formatted header"""
    width = 80
    print(f"\n{Colors.CYAN}{'═' * width}{Colors.END}")
    print(f"{Colors.CYAN}║{Colors.END} {Colors.BOLD}{text:^78}{Colors.END} {Colors.CYAN}║{Colors.END}")
    print(f"{Colors.CYAN}{'═' * width}{Colors.END}\n")

def print_section(text: str):
    """Print section divider"""
    print(f"\n{Colors.BLUE}{'─' * 80}{Colors.END}")
    print(f"{Colors.YELLOW}{Colors.BOLD}{text}{Colors.END}")
    print(f"{Colors.BLUE}{'─' * 80}{Colors.END}\n")

class MetricsCollector:
    """Collects metrics from Kubernetes cluster"""
    
    def __init__(self, namespace: str = "sock-shop"):
        self.namespace = namespace
        
    def run_command(self, cmd: List[str]) -> Tuple[str, int]:
        """Run shell command and return output"""
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10
            )
            return result.stdout.strip(), result.returncode
        except Exception as e:
            return f"Error: {e}", 1
    
    def get_pod_count(self) -> int:
        """Get running pod count"""
        output, _ = self.run_command([
            "kubectl", "get", "pods",
            "-n", self.namespace,
            "--field-selector=status.phase=Running",
            "--no-headers"
        ])
        if output and not output.startswith("Error"):
            return len(output.strip().split('\n'))
        return 0
    
    def get_hpa_status(self) -> Dict[str, any]:
        """Get HPA status"""
        output, _ = self.run_command([
            "kubectl", "get", "hpa",
            "-n", self.namespace,
            "--no-headers"
        ])
        
        hpas = {}
        if output and not output.startswith("Error"):
            for line in output.split('\n'):
                if line.strip():
                    parts = line.split()
                    if len(parts) >= 4:
                        name = parts[0]
                        replicas = parts[1]  # current/desired
                        hpas[name] = {
                            'replicas': replicas,
                            'max': parts[2] if len(parts) > 2 else 'N/A'
                        }
        return hpas
    
    def get_cpu_usage(self) -> Dict[str, float]:
        """Get CPU usage for pods"""
        output, _ = self.run_command([
            "kubectl", "top", "pods",
            "-n", self.namespace,
            "--no-headers"
        ])
        
        cpu_data = {}
        if output and not output.startswith("Error"):
            for line in output.split('\n'):
                if line.strip():
                    parts = line.split()
                    if len(parts) >= 2:
                        pod = parts[0]
                        cpu = parts[1].replace('m', '')
                        try:
                            cpu_data[pod] = float(cpu)
                        except ValueError:
                            pass
        return cpu_data
    
    def get_network_policies(self) -> int:
        """Get count of active network policies"""
        output, _ = self.run_command([
            "kubectl", "get", "networkpolicies",
            "-n", self.namespace,
            "--no-headers"
        ])
        if output and not output.startswith("Error"):
            return len(output.strip().split('\n')) if output.strip() else 0
        return 0
    
    def check_nephio_features(self) -> Dict[str, bool]:
        """Check which Nephio features are active"""
        features = {
            'dynamic_netpol': False,
            'predictive_hpa': False,
            'capacity_coordination': False,
            'nf_chain': False,
            'traffic_steering': False,
            'adaptive_rate_limit': False
        }
        
        # Check CRDs exist
        output, code = self.run_command([
            "kubectl", "get", "crd",
            "ddosprotections.workload.nephio.org"
        ])
        
        if code != 0:
            return features
        
        # Check specific resources
        resources = [
            ('dynamicnetworkpolicies', 'dynamic_netpol'),
            ('predictiveautoscaling', 'predictive_hpa'),
            ('capacityrequests', 'capacity_coordination'),
            ('networkfunctionchains', 'nf_chain'),
            ('dynamictrafficsteering', 'traffic_steering'),
            ('adaptiveratelimiting', 'adaptive_rate_limit')
        ]
        
        for resource, key in resources:
            output, code = self.run_command([
                "kubectl", "get", resource,
                "-n", self.namespace
            ])
            features[key] = (code == 0 and output and not output.startswith("Error"))
        
        return features
    
    def get_service_response_time(self, url: str) -> Optional[float]:
        """Measure service response time"""
        try:
            import requests
            start = time.time()
            response = requests.get(url, timeout=5)
            elapsed = time.time() - start
            return elapsed * 1000  # Convert to ms
        except:
            return None
    
    def get_error_rate(self, url: str, samples: int = 10) -> float:
        """Calculate error rate by sampling requests"""
        try:
            import requests
            errors = 0
            for _ in range(samples):
                try:
                    response = requests.get(url, timeout=2)
                    if response.status_code >= 400:
                        errors += 1
                except:
                    errors += 1
            return (errors / samples) * 100
        except:
            return 0.0

class AttackExecutor:
    """Executes crossfire attacks"""
    
    def __init__(self, target_url: str):
        self.target_url = target_url
    
    async def execute_attack(
        self,
        duration: int,
        workers: int,
        rate: int,
        use_decoy_discovery: bool = True
    ) -> Dict[str, any]:
        """Execute crossfire attack and return results"""
        
        print(f"  {Colors.YELLOW}⚡ Launching crossfire attack...{Colors.END}")
        print(f"     Workers: {workers}")
        print(f"     Rate: {rate} req/s per worker")
        print(f"     Duration: {duration}s")
        print(f"     Total load: {workers * rate} req/s\n")
        
        # Build command
        cmd = [
            "python3",
            "crossfire-app-level.py",
            "--url", self.target_url,
            "--duration", str(duration),
            "--rate", str(rate),
            "--workers", str(workers),
            "--non-interactive"
        ]
        
        if use_decoy_discovery and Path("discovered-endpoints.json").exists():
            cmd.extend(["--targets-file", "discovered-endpoints.json"])
            cmd.extend(["--decoys", "15"])
        
        # Execute attack
        start_time = time.time()
        
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            # Wait for attack to complete
            stdout, stderr = await process.communicate()
            
            elapsed_time = time.time() - start_time
            
            # Parse results
            results = {
                'duration': elapsed_time,
                'success': process.returncode == 0,
                'output': stdout.decode() if stdout else '',
                'errors': stderr.decode() if stderr else ''
            }
            
            return results
            
        except Exception as e:
            return {
                'duration': time.time() - start_time,
                'success': False,
                'output': '',
                'errors': str(e)
            }

class TestRunner:
    """Runs the 3-phase testing"""
    
    def __init__(
        self,
        target_url: str,
        namespace: str,
        attack_duration: int,
        attack_workers: int,
        attack_rate: int,
        non_interactive: bool = False
    ):
        self.target_url = target_url
        self.namespace = namespace
        self.attack_duration = attack_duration
        self.attack_workers = attack_workers
        self.attack_rate = attack_rate
        self.non_interactive = non_interactive
        self.metrics = MetricsCollector(namespace)
        self.attacker = AttackExecutor(target_url)
        self.results = {}
        self.results = {
            'baseline': {},
            'native': {},
            'nephio': {}
        }
    
    def collect_metrics_snapshot(self, label: str) -> Dict[str, any]:
        """Collect all metrics at a point in time"""
        print(f"  {Colors.CYAN}📊 Collecting metrics ({label})...{Colors.END}")
        
        snapshot = {
            'timestamp': datetime.now().isoformat(),
            'label': label,
            'pods': self.metrics.get_pod_count(),
            'hpa': self.metrics.get_hpa_status(),
            'cpu': self.metrics.get_cpu_usage(),
            'network_policies': self.metrics.get_network_policies(),
            'response_time': self.metrics.get_service_response_time(self.target_url),
            'error_rate': self.metrics.get_error_rate(self.target_url),
            'nephio_features': self.metrics.check_nephio_features()
        }
        
        return snapshot
    
    def display_snapshot(self, snapshot: Dict[str, any]):
        """Display metrics snapshot in readable format"""
        print(f"\n  {Colors.BOLD}Metrics Snapshot:{Colors.END}")
        print(f"    Running Pods: {colored(str(snapshot['pods']), Colors.GREEN)}")
        
        if snapshot['hpa']:
            print(f"    HPA Status:")
            for name, data in snapshot['hpa'].items():
                print(f"      - {name}: {data['replicas']} (max: {data['max']})")
        
        total_cpu = sum(snapshot['cpu'].values()) if snapshot['cpu'] else 0
        cpu_color = Colors.RED if total_cpu > 2000 else Colors.YELLOW if total_cpu > 1000 else Colors.GREEN
        print(f"    Total CPU: {colored(f'{total_cpu:.0f}m', cpu_color)}")
        
        print(f"    Network Policies: {snapshot['network_policies']}")
        
        if snapshot['response_time']:
            rt_val = snapshot['response_time']
            rt_color = Colors.RED if rt_val > 1000 else Colors.YELLOW if rt_val > 500 else Colors.GREEN
            print(f"    Response Time: {colored(f'{rt_val:.2f}ms', rt_color)}")
        
        err_val = snapshot['error_rate']
        err_color = Colors.RED if err_val > 10 else Colors.YELLOW if err_val > 5 else Colors.GREEN
        print(f"    Error Rate: {colored(f'{err_val:.1f}%', err_color)}")
        
        nephio_count = sum(1 for v in snapshot['nephio_features'].values() if v)
        if nephio_count > 0:
            print(f"    Nephio Features: {colored(f'{nephio_count} active', Colors.MAGENTA)}")
            for feature, active in snapshot['nephio_features'].items():
                if active:
                    print(f"      - {feature}: {colored('✓', Colors.GREEN)}")
    
    async def run_test_case(
        self,
        case_name: str,
        phase: str,
        setup_func=None
    ) -> Dict[str, any]:
        """Run a single test case"""
        
        print_section(f"Phase: {case_name}")
        
        # Setup (deploy mitigations if needed)
        if setup_func:
            print(f"  {Colors.YELLOW}🔧 Setting up mitigations...{Colors.END}\n")
            setup_result = setup_func()
            if not setup_result:
                print(f"  {Colors.RED}✗ Setup failed{Colors.END}\n")
                return {'success': False, 'error': 'Setup failed'}
            print(f"  {Colors.GREEN}✓ Setup complete{Colors.END}\n")
            # Wait for resources to stabilize
            print(f"  {Colors.YELLOW}⏳ Waiting 30s for resources to stabilize...{Colors.END}\n")
            await asyncio.sleep(30)
        
        # Pre-attack metrics
        print(f"  {Colors.BOLD}BEFORE ATTACK{Colors.END}")
        pre_metrics = self.collect_metrics_snapshot("pre-attack")
        self.display_snapshot(pre_metrics)
        
        print(f"\n  {Colors.RED}{'━' * 76}{Colors.END}")
        print(f"  {Colors.RED}{Colors.BOLD}🚨 ATTACK IN PROGRESS 🚨{Colors.END}")
        print(f"  {Colors.RED}{'━' * 76}{Colors.END}\n")
        
        # Execute attack
        attack_results = await self.attacker.execute_attack(
            self.attack_duration,
            self.attack_workers,
            self.attack_rate
        )
        
        # Wait for metrics to settle
        print(f"\n  {Colors.YELLOW}⏳ Waiting 15s for metrics to settle...{Colors.END}\n")
        await asyncio.sleep(15)
        
        # Post-attack metrics
        print(f"  {Colors.BOLD}AFTER ATTACK{Colors.END}")
        post_metrics = self.collect_metrics_snapshot("post-attack")
        self.display_snapshot(post_metrics)
        
        # Calculate impact
        impact = self.calculate_impact(pre_metrics, post_metrics)
        
        # Display impact analysis
        print(f"\n  {Colors.BOLD}{Colors.CYAN}IMPACT ANALYSIS{Colors.END}")
        self.display_impact(impact)
        
        results = {
            'success': True,
            'pre_metrics': pre_metrics,
            'post_metrics': post_metrics,
            'attack_results': attack_results,
            'impact': impact
        }
        
        self.results[phase] = results
        
        return results
    
    def calculate_impact(
        self,
        pre: Dict[str, any],
        post: Dict[str, any]
    ) -> Dict[str, any]:
        """Calculate attack impact metrics"""
        
        impact = {}
        
        # Pod scaling
        impact['pod_increase'] = post['pods'] - pre['pods']
        impact['pod_increase_pct'] = (
            (post['pods'] - pre['pods']) / pre['pods'] * 100
            if pre['pods'] > 0 else 0
        )
        
        # CPU increase
        pre_cpu = sum(pre['cpu'].values()) if pre['cpu'] else 0
        post_cpu = sum(post['cpu'].values()) if post['cpu'] else 0
        impact['cpu_increase'] = post_cpu - pre_cpu
        impact['cpu_increase_pct'] = (
            (post_cpu - pre_cpu) / pre_cpu * 100
            if pre_cpu > 0 else 0
        )
        
        # Response time degradation
        if pre['response_time'] and post['response_time']:
            impact['response_time_increase'] = post['response_time'] - pre['response_time']
            impact['response_time_increase_pct'] = (
                (post['response_time'] - pre['response_time']) / pre['response_time'] * 100
            )
        else:
            impact['response_time_increase'] = 0
            impact['response_time_increase_pct'] = 0
        
        # Error rate increase
        impact['error_rate_increase'] = post['error_rate'] - pre['error_rate']
        
        # Service availability
        if post['response_time'] and post['response_time'] < 5000 and post['error_rate'] < 50:
            impact['service_available'] = True
        else:
            impact['service_available'] = False
        
        # Overall mitigation effectiveness (0-100)
        # Lower error rate and response time = better mitigation
        if impact['service_available']:
            effectiveness = 100 - min(post['error_rate'], 100)
            if post['response_time']:
                # Penalize high response times
                if post['response_time'] > 1000:
                    effectiveness *= 0.8
                elif post['response_time'] > 500:
                    effectiveness *= 0.9
            impact['mitigation_effectiveness'] = effectiveness
        else:
            impact['mitigation_effectiveness'] = 0
        
        return impact
    
    def display_impact(self, impact: Dict[str, any]):
        """Display impact analysis"""
        
        # Pod scaling
        pod_inc = impact['pod_increase']
        pod_inc_pct = impact['pod_increase_pct']
        pod_color = Colors.GREEN if pod_inc > 0 else Colors.YELLOW
        print(f"    Pod Scaling: {colored(f'+{pod_inc}', pod_color)} " +
              f"({pod_inc_pct:+.1f}%)")
        
        # CPU increase
        cpu_inc = impact['cpu_increase']
        cpu_inc_pct = impact['cpu_increase_pct']
        cpu_color = Colors.RED if cpu_inc > 1000 else Colors.YELLOW if cpu_inc > 500 else Colors.GREEN
        print(f"    CPU Impact: {colored(f'+{cpu_inc:.0f}m', cpu_color)} " +
              f"({cpu_inc_pct:+.1f}%)")
        
        # Response time
        rt_inc = impact['response_time_increase']
        if rt_inc != 0:
            rt_inc_pct = impact['response_time_increase_pct']
            rt_color = Colors.RED if rt_inc > 500 else Colors.YELLOW if rt_inc > 200 else Colors.GREEN
            print(f"    Response Time: {colored(f'+{rt_inc:.2f}ms', rt_color)} " +
                  f"({rt_inc_pct:+.1f}%)")
        
        # Error rate
        err_inc = impact['error_rate_increase']
        err_color = Colors.RED if err_inc > 20 else Colors.YELLOW if err_inc > 10 else Colors.GREEN
        print(f"    Error Rate: {colored(f'+{err_inc:.1f}%', err_color)}")
        
        # Service availability
        avail_color = Colors.GREEN if impact['service_available'] else Colors.RED
        avail_text = "AVAILABLE" if impact['service_available'] else "DEGRADED/DOWN"
        print(f"    Service Status: {colored(avail_text, avail_color)}")
        
        # Mitigation effectiveness
        eff = impact['mitigation_effectiveness']
        eff_color = Colors.GREEN if eff > 80 else Colors.YELLOW if eff > 50 else Colors.RED
        print(f"    Mitigation Effectiveness: {colored(f'{eff:.1f}%', eff_color)}")
    
    def deploy_native_mitigations(self) -> bool:
        """Deploy native Kubernetes mitigations"""
        try:
            # Apply native mitigations
            mitigation_base = os.getenv('MITIGATION_PATH', '../mitigation')
            mitigations_path = Path(mitigation_base) / "kubernetes-native"
            
            if not mitigations_path.exists():
                print(f"    {Colors.RED}✗ Mitigations directory not found{Colors.END}")
                return False
            
            # Deploy in order
            components = [
                "resource-quotas",
                "network-policies",
                "autoscaling"
            ]
            
            for component in components:
                component_path = mitigations_path / component
                if component_path.exists():
                    print(f"    - Deploying {component}...")
                    result = subprocess.run(
                        ["kubectl", "apply", "-f", str(component_path)],
                        capture_output=True,
                        text=True
                    )
                    if result.returncode != 0:
                        print(f"      {Colors.YELLOW}⚠ Some resources may already exist{Colors.END}")
            
            return True
            
        except Exception as e:
            print(f"    {Colors.RED}✗ Error deploying native mitigations: {e}{Colors.END}")
            return False
    
    def deploy_nephio_mitigations(self) -> bool:
        """Deploy Nephio mitigations"""
        try:
            mitigation_base = os.getenv('MITIGATION_PATH', '../mitigation')
            nephio_path = Path(mitigation_base) / "nephio/packages/crossfire-protection-package"
            
            if not nephio_path.exists():
                print(f"    {Colors.RED}✗ Nephio package not found{Colors.END}")
                return False
            
            deploy_script = nephio_path / "deploy.sh"
            if not deploy_script.exists():
                print(f"    {Colors.RED}✗ Nephio deploy script not found{Colors.END}")
                return False
            
            print(f"    - Running Nephio deployment...")
            result = subprocess.run(
                [str(deploy_script)],
                cwd=str(nephio_path),
                capture_output=True,
                text=True,
                env={**os.environ, 'TARGET_NAMESPACE': self.namespace}
            )
            
            if result.returncode != 0:
                print(f"      {Colors.YELLOW}⚠ Deployment completed with warnings{Colors.END}")
            
            return True
            
        except Exception as e:
            print(f"    {Colors.RED}✗ Error deploying Nephio mitigations: {e}{Colors.END}")
            return False
    
    def display_comparison(self):
        """Display comparison of all three phases"""
        
        print_header("COMPARATIVE ANALYSIS")
        
        phases = ['baseline', 'native', 'nephio']
        phase_names = ['Baseline (No Mitigations)', 'Native Kubernetes', 'Nephio Enhanced']
        
        # Check which phases have data
        available_phases = [p for p in phases if self.results.get(p) and self.results[p].get('success')]
        
        if not available_phases:
            print(f"{Colors.RED}No test results available for comparison{Colors.END}")
            return
        
        print(f"\n{Colors.BOLD}Summary Table:{Colors.END}\n")
        
        # Table header
        print(f"  {'Metric':<30} ", end='')
        for phase in available_phases:
            idx = phases.index(phase)
            print(f"{phase_names[idx]:<25} ", end='')
        print()
        
        print(f"  {'-' * 30} ", end='')
        for _ in available_phases:
            print(f"{'-' * 25} ", end='')
        print()
        
        # Metrics rows
        metrics_to_compare = [
            ('Service Availability', lambda r: '✓ Available' if r['impact']['service_available'] else '✗ Degraded'),
            ('Error Rate Increase', lambda r: f"+{r['impact']['error_rate_increase']:.1f}%"),
            ('Response Time Increase', lambda r: f"+{r['impact']['response_time_increase']:.0f}ms"),
            ('CPU Increase', lambda r: f"+{r['impact']['cpu_increase']:.0f}m"),
            ('Pod Scaling', lambda r: f"+{r['impact']['pod_increase']}"),
            ('Mitigation Effectiveness', lambda r: f"{r['impact']['mitigation_effectiveness']:.1f}%"),
        ]
        
        for metric_name, metric_func in metrics_to_compare:
            print(f"  {metric_name:<30} ", end='')
            for phase in available_phases:
                result = self.results[phase]
                try:
                    value = metric_func(result)
                    print(f"{value:<25} ", end='')
                except:
                    print(f"{'N/A':<25} ", end='')
            print()
        
        print()
        
        # Detailed comparison
        print(f"\n{Colors.BOLD}Detailed Comparison:{Colors.END}\n")
        
        for i, phase in enumerate(available_phases):
            result = self.results[phase]
            impact = result['impact']
            
            print(f"  {Colors.BOLD}{phase_names[phases.index(phase)]}{Colors.END}")
            print(f"  {'─' * 76}")
            
            if impact['service_available']:
                print(f"    Service Status: {colored('✓ AVAILABLE', Colors.GREEN)}")
            else:
                print(f"    Service Status: {colored('✗ DEGRADED/DOWN', Colors.RED)}")
            
            print(f"    Error Rate Impact: {impact['error_rate_increase']:+.1f}%")
            print(f"    Response Time Impact: {impact['response_time_increase']:+.0f}ms")
            print(f"    Mitigation Effectiveness: {impact['mitigation_effectiveness']:.1f}%")
            
            # Show active protections
            post = result['post_metrics']
            print(f"\n    Active Protections:")
            print(f"      - Running Pods: {post['pods']}")
            if post['hpa']:
                print(f"      - HPA Active: {len(post['hpa'])} autoscalers")
            print(f"      - Network Policies: {post['network_policies']}")
            
            nephio_count = sum(1 for v in post['nephio_features'].values() if v)
            if nephio_count > 0:
                print(f"      - Nephio Features: {nephio_count} active")
                for feature, active in post['nephio_features'].items():
                    if active:
                        print(f"          • {feature}")
            
            print()
        
        # Key findings
        print(f"\n{Colors.BOLD}{Colors.CYAN}Key Findings:{Colors.END}\n")
        
        if 'baseline' in available_phases and 'native' in available_phases:
            baseline_eff = self.results['baseline']['impact']['mitigation_effectiveness']
            native_eff = self.results['native']['impact']['mitigation_effectiveness']
            improvement = native_eff - baseline_eff
            
            if improvement > 0:
                print(f"  {Colors.GREEN}✓{Colors.END} Native K8s improved effectiveness by {colored(f'+{improvement:.1f}%', Colors.GREEN)}")
            else:
                print(f"  {Colors.RED}✗{Colors.END} Native K8s showed minimal improvement")
        
        if 'native' in available_phases and 'nephio' in available_phases:
            native_eff = self.results['native']['impact']['mitigation_effectiveness']
            nephio_eff = self.results['nephio']['impact']['mitigation_effectiveness']
            improvement = nephio_eff - native_eff
            
            if improvement > 0:
                print(f"  {Colors.GREEN}✓{Colors.END} Nephio improved over native by {colored(f'+{improvement:.1f}%', Colors.GREEN)}")
            
            # Features unique to Nephio
            native_features = self.results['native']['post_metrics']['nephio_features']
            nephio_features = self.results['nephio']['post_metrics']['nephio_features']
            
            unique_nephio = [k for k, v in nephio_features.items() if v and not native_features.get(k)]
            if unique_nephio:
                print(f"\n  {Colors.MAGENTA}Nephio-Exclusive Features:{Colors.END}")
                for feature in unique_nephio:
                    print(f"    • {feature}")
        
        # Attack impact comparison
        if len(available_phases) >= 2:
            print(f"\n  {Colors.BOLD}Attack Impact Reduction:{Colors.END}")
            
            baseline_err = self.results[available_phases[0]]['impact']['error_rate_increase']
            
            for phase in available_phases[1:]:
                phase_err = self.results[phase]['impact']['error_rate_increase']
                reduction = baseline_err - phase_err
                if reduction > 0:
                    print(f"    {phase_names[phases.index(phase)]}: {colored(f'-{reduction:.1f}%', Colors.GREEN)} error rate reduction")
        
        print()
    
    async def run_all_tests(self):
        """Run all three test phases"""
        
        print_header("CROSSFIRE ATTACK MITIGATION TEST FRAMEWORK")
        
        print(f"{Colors.BOLD}Configuration:{Colors.END}")
        print(f"  Target: {self.target_url}")
        print(f"  Namespace: {self.namespace}")
        print(f"  Attack Duration: {self.attack_duration}s")
        print(f"  Attack Workers: {self.attack_workers}")
        print(f"  Attack Rate: {self.attack_rate} req/s/worker")
        print(f"  Total Load: {self.attack_workers * self.attack_rate} req/s")
        print()
        
        if not self.non_interactive:
            input(f"{Colors.YELLOW}Press Enter to start Phase 1: Baseline Attack...{Colors.END}")
        else:
            print(f"{Colors.YELLOW}Starting Phase 1: Baseline Attack...{Colors.END}")
        
        # Phase 1: Baseline (no mitigations)
        print_header("PHASE 1: BASELINE ATTACK (No Mitigations)")
        print(f"{Colors.YELLOW}This establishes the baseline impact of crossfire attacks.{Colors.END}\n")
        
        await self.run_test_case(
            "Baseline - No Mitigations Active",
            "baseline"
        )
        
        if not self.non_interactive:
            input(f"\n{Colors.YELLOW}Press Enter to start Phase 2: Native Kubernetes Mitigations...{Colors.END}")
        else:
            print(f"\n{Colors.YELLOW}Starting Phase 2: Native Kubernetes Mitigations...{Colors.END}")
        
        # Phase 2: Native K8s mitigations
        print_header("PHASE 2: NATIVE KUBERNETES MITIGATIONS")
        print(f"{Colors.YELLOW}Testing HPA, Network Policies, Resource Quotas, and Istio rate limiting.{Colors.END}\n")
        
        await self.run_test_case(
            "Native Kubernetes Mitigations",
            "native",
            setup_func=self.deploy_native_mitigations
        )
        
        if not self.non_interactive:
            input(f"\n{Colors.YELLOW}Press Enter to start Phase 3: Nephio-Enhanced Mitigations...{Colors.END}")
        else:
            print(f"\n{Colors.YELLOW}Starting Phase 3: Nephio-Enhanced Mitigations...{Colors.END}")
        
        # Phase 3: Nephio mitigations
        print_header("PHASE 3: NEPHIO-ENHANCED MITIGATIONS")
        print(f"{Colors.YELLOW}Testing ML-based detection, predictive scaling, multi-cluster coordination.{Colors.END}\n")
        
        await self.run_test_case(
            "Nephio-Enhanced Mitigations",
            "nephio",
            setup_func=self.deploy_nephio_mitigations
        )
        
        # Display comparison
        self.display_comparison()
        
        # Save results
        self.save_results()
        
        print_header("TEST COMPLETE")
        print(f"{Colors.GREEN}All phases completed successfully!{Colors.END}")
        print(f"{Colors.CYAN}Results saved to: crossfire-test-results.json{Colors.END}\n")
    
    def save_results(self):
        """Save results to JSON file"""
        output = {
            'test_info': {
                'timestamp': datetime.now().isoformat(),
                'target_url': self.target_url,
                'namespace': self.namespace,
                'attack_config': {
                    'duration': self.attack_duration,
                    'workers': self.attack_workers,
                    'rate': self.attack_rate,
                    'total_load': self.attack_workers * self.attack_rate
                }
            },
            'results': self.results
        }
        
        with open('crossfire-test-results.json', 'w') as f:
            json.dump(output, f, indent=2)

async def main():
    parser = argparse.ArgumentParser(
        description='Comprehensive Crossfire Attack Testing Framework'
    )
    parser.add_argument(
        '--target-url',
        default='http://192.168.49.2:30001',
        help='Target service URL'
    )
    parser.add_argument(
        '--namespace',
        default='sock-shop',
        help='Kubernetes namespace'
    )
    parser.add_argument(
        '--attack-duration',
        type=int,
        default=60,
        help='Attack duration in seconds'
    )
    parser.add_argument(
        '--attack-workers',
        type=int,
        default=50,
        help='Number of attack workers'
    )
    parser.add_argument(
        '--attack-rate',
        type=int,
        default=20,
        help='Requests per second per worker'
    )
    parser.add_argument(
        '--non-interactive',
        action='store_true',
        help='Run without user input prompts'
    )
    
    args = parser.parse_args()
    
    # Verify we're in the right directory
    if not Path('crossfire-app-level.py').exists():
        print(f"{Colors.RED}Error: Must run from attack-simulations directory{Colors.END}")
        sys.exit(1)
    
    # Create test runner
    runner = TestRunner(
        args.target_url,
        args.namespace,
        args.attack_duration,
        args.attack_workers,
        args.attack_rate,
        args.non_interactive
    )
    
    # Run all tests
    await runner.run_all_tests()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n\n{Colors.YELLOW}Test interrupted by user{Colors.END}")
        sys.exit(1)
