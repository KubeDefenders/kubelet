#!/usr/bin/env python3
import requests
import random
import time
import threading
from loguru import logger
import argparse


class RealisticUser:
    """Simulates a realistic e-commerce user session"""
    
    # Real Sock Shop product IDs
    PRODUCT_IDS = [
        "03fef6ac-1896-4ce8-bd69-b798f85c6e0b",
        "510a0d7e-8e83-4193-b483-e27e09ddc34d",
        "808a2de1-1aaa-4c25-a9b9-6612e8f29a38",
        "819e1fbf-8b7e-4f6d-811f-693534916a8b",
        "837ab141-399e-4c1f-9abc-bace40296bac",
        "a0a4f044-b040-410d-8ead-4de0446aec7e",
        "d3588630-ad8e-49df-bbd7-3167f7efb246"
    ]
    
    def __init__(self, base_url: str, user_type: str = "normal"):
        self.base_url = base_url.rstrip('/')
        self.user_type = user_type  # "normal" or "intensive"
        self.cart_items = []
        self.stats = {'requests': 0, 'errors': 0}
        
        # Think time ranges based on user type
        if user_type == "intensive":
            self.think_time = (0.5, 2.0)  # Power user - faster
        else:
            self.think_time = (1.0, 5.0)  # Normal user - realistic pauses
    
    def _request(self, endpoint: str, method: str = 'GET', **kwargs):
        """Make HTTP request with error handling"""
        try:
            url = f"{self.base_url}{endpoint}"
            if method == 'GET':
                response = requests.get(url, timeout=5, **kwargs)
            else:
                response = requests.post(url, timeout=5, **kwargs)
            
            self.stats['requests'] += 1
            return response.status_code < 500
        except Exception as e:
            self.stats['errors'] += 1
            logger.debug(f"Request error: {e}")
            return False
    
    def _think(self):
        """Simulate user think time"""
        time.sleep(random.uniform(*self.think_time))
    
    def browse_home(self):
        """Visit homepage - Weight 10 (most common)"""
        self._request('/')
        self._think()
    
    def view_catalogue(self):
        """Browse catalogue - Weight 8"""
        self._request('/catalogue')
        self._think()
        self._request('/category.html')
        self._think()
    
    def view_product(self):
        """View specific product - Weight 6"""
        product_id = random.choice(self.PRODUCT_IDS)
        self._request(f'/detail.html?id={product_id}')
        self._think()
    
    def add_to_cart(self):
        """Add item to cart - Weight 3"""
        # View product first
        product_id = random.choice(self.PRODUCT_IDS)
        self._request(f'/detail.html?id={product_id}')
        self._think()
        
        # Add to cart
        self._request('/cart', method='POST', json={"id": product_id, "quantity": 1})
        self.cart_items.append(product_id)
        self._think()
    
    def view_cart(self):
        """View shopping cart - Weight 2"""
        self._request('/basket.html')
        self._think()
    
    def checkout_flow(self):
        """Checkout flow - Weight 1 (conversion funnel)"""
        # Ensure cart has items
        if not self.cart_items:
            self.add_to_cart()
        
        # View cart before checkout
        self._request('/basket.html')
        self._think()
        
        # Simulate checkout page visit
        # (Real checkout would require auth)
        self._request('/customer-order.html')
        self._think()
    
    def browse_with_filters(self):
        """Browse with filters - Weight 4"""
        # Apply sorting
        sort = random.choice(['price', 'name', 'newest'])
        self._request(f'/catalogue?sort={sort}')
        self._think()
        
        # Pagination
        page = random.randint(1, 3)
        self._request(f'/catalogue?page={page}')
        self._think()
    
    def rapid_browse(self):
        """Power user rapid browsing"""
        self._request('/')
        time.sleep(0.3)
        self._request('/catalogue')
        time.sleep(0.3)
        self._request('/category.html')
        self._think()
    
    def view_multiple_products(self):
        """Power user views multiple products"""
        for product_id in random.sample(self.PRODUCT_IDS, 3):
            self._request(f'/detail.html?id={product_id}')
            time.sleep(random.uniform(0.3, 1.0))
        self._think()
    
    def run_session(self):
        """Run a complete user session with weighted task selection"""
        if self.user_type == "intensive":
            # Power user - more rapid actions
            tasks = [
                (self.rapid_browse, 15),
                (self.view_multiple_products, 5)
            ]
        else:
            # Normal user - realistic e-commerce behavior
            tasks = [
                (self.browse_home, 10),
                (self.view_catalogue, 8),
                (self.view_product, 6),
                (self.browse_with_filters, 4),
                (self.add_to_cart, 3),
                (self.view_cart, 2),
                (self.checkout_flow, 1)
            ]
        
        # Choose actions based on weights
        actions = []
        for task, weight in tasks:
            actions.extend([task] * weight)
        
        # Execute random number of actions (realistic session length)
        num_actions = random.randint(3, 8)
        for _ in range(num_actions):
            task = random.choice(actions)
            task()


class RealisticTrafficGenerator:
    """Manages multiple realistic user sessions"""
    
    def __init__(self, base_url: str, num_users: int = 10, user_mix: float = 0.2):
        self.base_url = base_url
        self.num_users = num_users
        self.user_mix = user_mix  # Percentage of intensive users (default 20%)
        self.running = False
        self.threads = []
        self.total_stats = {'requests': 0, 'errors': 0}
    
    def worker(self, user_id: int):
        """Worker simulating a continuous user"""
        # Determine user type
        user_type = "intensive" if random.random() < self.user_mix else "normal"
        user = RealisticUser(self.base_url, user_type)
        
        logger.info(f"User {user_id} started ({user_type} behavior)")
        
        while self.running:
            try:
                user.run_session()
                
                # Session gap (user leaves and comes back)
                time.sleep(random.uniform(2, 8))
                
            except Exception as e:
                logger.debug(f"Worker {user_id} error: {e}")
        
        # Update total stats
        self.total_stats['requests'] += user.stats['requests']
        self.total_stats['errors'] += user.stats['errors']
    
    def start(self, duration: int = None):
        """Start realistic traffic generation"""
        logger.info(f"Starting {self.num_users} realistic users ({int(self.user_mix*100)}% power users)")
        self.running = True
        
        # Spawn users gradually (spawn rate)
        spawn_rate = 2  # users per second
        for i in range(self.num_users):
            thread = threading.Thread(target=self.worker, args=(i+1,))
            thread.daemon = True
            thread.start()
            self.threads.append(thread)
            
            if i < self.num_users - 1:
                time.sleep(1.0 / spawn_rate)
        
        logger.success(f"All {self.num_users} users spawned and active")
        
        # Run for specified duration
        if duration:
            time.sleep(duration)
            self.stop()
    
    def stop(self):
        """Stop traffic generation"""
        logger.info("Stopping traffic generator...")
        self.running = False
        
        # Wait for threads
        for thread in self.threads:
            thread.join(timeout=2)
        
        logger.success(f"Stopped. Total requests: {self.total_stats['requests']}, Errors: {self.total_stats['errors']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Realistic traffic generator for Sock Shop")
    parser.add_argument("--url", default="http://192.168.49.2:30001", help="Base URL")
    parser.add_argument("--users", type=int, default=10, help="Number of concurrent users")
    parser.add_argument("--duration", type=int, help="Duration in seconds (omit for continuous)")
    parser.add_argument("--intensive-ratio", type=float, default=0.2, help="Ratio of power users (0.0-1.0)")
    
    args = parser.parse_args()
    
    generator = RealisticTrafficGenerator(
        base_url=args.url,
        num_users=args.users,
        user_mix=args.intensive_ratio
    )
    
    try:
        generator.start(duration=args.duration)
        
        if not args.duration:
            # Run continuously until Ctrl+C
            logger.info("Running continuously. Press Ctrl+C to stop.")
            while generator.running:
                time.sleep(1)
    except KeyboardInterrupt:
        generator.stop()
