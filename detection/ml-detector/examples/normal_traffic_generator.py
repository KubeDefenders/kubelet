#!/usr/bin/env python3
"""
Normal Traffic Generator for Sock Shop
Simulates realistic user behavior
"""

import requests
import random
import time
import threading
from loguru import logger
import argparse


class NormalTrafficGenerator:
    """Generate realistic normal traffic to Sock Shop"""
    
    def __init__(self, base_url: str, rate: int = 5):
        self.base_url = base_url.rstrip('/')
        self.rate = rate  # requests per second
        self.running = False
        self.stats = {'total': 0, 'success': 0, 'errors': 0}
        
    def make_request(self, endpoint: str, method: str = 'GET'):
        """Make a single request"""
        try:
            url = f"{self.base_url}{endpoint}"
            if method == 'GET':
                response = requests.get(url, timeout=5)
            else:
                response = requests.post(url, timeout=5)
            
            self.stats['total'] += 1
            if response.status_code < 500:
                self.stats['success'] += 1
            else:
                self.stats['errors'] += 1
            return True
        except Exception as e:
            self.stats['total'] += 1
            self.stats['errors'] += 1
            return False
    
    def user_session(self):
        """Simulate a realistic user session"""
        # Browse catalog
        self.make_request('/')
        time.sleep(random.uniform(0.5, 2))
        
        self.make_request('/category.html')
        time.sleep(random.uniform(0.5, 2))
        
        # View some items
        for _ in range(random.randint(2, 5)):
            self.make_request('/detail.html?id=3395a43e-2d88-40de-b95f-e00e1502085b')
            time.sleep(random.uniform(0.3, 1.5))
        
        # Maybe add to cart
        if random.random() < 0.7:
            self.make_request('/basket.html')
            time.sleep(random.uniform(0.5, 2))
        
        # Maybe checkout
        if random.random() < 0.3:
            self.make_request('/customer-order.html')
    
    def worker(self):
        """Worker thread to generate traffic"""
        while self.running:
            try:
                self.user_session()
                # Wait between sessions
                time.sleep(1.0 / self.rate)
            except Exception as e:
                logger.debug(f"Error in worker: {e}")
    
    def start(self, num_workers: int = 3):
        """Start generating traffic"""
        logger.info(f"Starting normal traffic generator with {num_workers} workers at {self.rate} req/s each")
        self.running = True
        
        threads = []
        for i in range(num_workers):
            t = threading.Thread(target=self.worker, daemon=True)
            t.start()
            threads.append(t)
        
        return threads
    
    def stop(self):
        """Stop generating traffic"""
        logger.info("Stopping traffic generator")
        self.running = False
    
    def get_stats(self):
        """Get traffic statistics"""
        return self.stats.copy()


def main():
    parser = argparse.ArgumentParser(description='Generate normal traffic to Sock Shop')
    parser.add_argument('--url', default='http://192.168.49.2:30001', help='Sock Shop URL')
    parser.add_argument('--rate', type=int, default=5, help='Requests per second per worker')
    parser.add_argument('--workers', type=int, default=3, help='Number of worker threads')
    parser.add_argument('--duration', type=int, default=0, help='Duration in seconds (0 = infinite)')
    args = parser.parse_args()
    
    generator = NormalTrafficGenerator(args.url, args.rate)
    threads = generator.start(args.workers)
    
    try:
        start_time = time.time()
        while True:
            time.sleep(10)
            stats = generator.get_stats()
            elapsed = time.time() - start_time
            logger.info(f"Traffic stats: {stats['total']} total, {stats['success']} success, "
                       f"{stats['errors']} errors, {stats['total']/elapsed:.1f} req/s avg")
            
            if args.duration > 0 and elapsed >= args.duration:
                break
    
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    
    finally:
        generator.stop()
        time.sleep(2)
        stats = generator.get_stats()
        logger.info(f"Final stats: {stats}")


if __name__ == "__main__":
    main()
