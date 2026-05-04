#!/home/spuggle/dev/ddos/venv/bin/python3
"""Continuous Normal Traffic Generator"""

import asyncio
import aiohttp
import random
import argparse
from datetime import datetime


class NormalTrafficGenerator:
    def __init__(self, target_url: str, workers: int = 5, rate: float = 10.0):
        self.target_url = target_url
        self.workers = workers
        self.rate = rate
        self.endpoints = ['/', '/category.html', '/catalogue', '/basket.html', '/login']
        self.stats = {'requests': 0, 'errors': 0}
    
    async def worker(self, worker_id: int):
        delay = 1.0 / self.rate
        
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            while True:
                try:
                    endpoint = random.choice(self.endpoints)
                    url = f"{self.target_url}{endpoint}"
                    
                    async with session.get(url) as response:
                        await response.text()
                        self.stats['requests'] += 1
                        
                        if self.stats['requests'] % 100 == 0:
                            print(f"[{datetime.now().strftime('%H:%M:%S')}] Sent {self.stats['requests']} requests "
                                  f"({self.stats['errors']} errors)", flush=True)
                    
                    actual_delay = delay * random.uniform(0.8, 1.2)
                    await asyncio.sleep(actual_delay)
                    
                except Exception as e:
                    self.stats['errors'] += 1
                    await asyncio.sleep(1)
    
    async def run(self):
        print(f"\n{'='*60}")
        print(f"Normal Traffic Generator")
        print(f"{'='*60}")
        print(f"Target: {self.target_url}")
        print(f"Workers: {self.workers}")
        print(f"Rate: {self.rate} req/s per worker")
        print(f"Total Rate: ~{self.workers * self.rate} req/s")
        print(f"Endpoints: {len(self.endpoints)}")
        print(f"Started: {datetime.now().strftime('%H:%M:%S')}")
        print(f"{'='*60}\n")
        print("Press Ctrl+C to stop\n")
        
        tasks = [self.worker(i) for i in range(self.workers)]
        await asyncio.gather(*tasks)


def main():
    parser = argparse.ArgumentParser(description='Normal Traffic Generator')
    parser.add_argument('--target-url', required=True, help='Target URL')
    parser.add_argument('--workers', type=int, default=5, help='Number of workers')
    parser.add_argument('--rate', type=float, default=10.0, help='Requests per second per worker')
    
    args = parser.parse_args()
    generator = NormalTrafficGenerator(args.target_url, args.workers, args.rate)
    
    try:
        asyncio.run(generator.run())
    except KeyboardInterrupt:
        print(f"\n\nStopped. Total: {generator.stats['requests']} requests, {generator.stats['errors']} errors\n")


if __name__ == "__main__":
    main()
