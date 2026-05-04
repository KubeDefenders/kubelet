#!/usr/bin/env python3
"""
Locust Load Test for Sock Shop
Simulates realistic e-commerce user behavior
"""

from locust import HttpUser, task, between
import random


class SockShopUser(HttpUser):
    """
    Simulates realistic Sock Shop user behavior with weighted tasks
    representing typical e-commerce shopping patterns
    """
    
    # Wait time between tasks (simulates user "think time")
    wait_time = between(1, 5)  # 1-5 seconds between actions
    
    def on_start(self):
        """Called when a user starts - simulates user arriving at site"""
        self.session_id = None
        self.cart_items = []
        
    @task(10)  # Weight 10: Most common action - browsing
    def browse_home(self):
        """Visit homepage"""
        self.client.get("/", name="Home Page")
    
    @task(8)  # Weight 8: View catalogue
    def view_catalogue(self):
        """Browse product catalogue"""
        self.client.get("/catalogue", name="Catalogue")
        self.client.get("/category.html", name="Category Page")
    
    @task(6)  # Weight 6: View specific product
    def view_product(self):
        """View individual product details"""
        # Sock Shop has product IDs like 03fef6ac-1896-4ce8-bd69-b798f85c6e0b
        product_ids = [
            "03fef6ac-1896-4ce8-bd69-b798f85c6e0b",
            "510a0d7e-8e83-4193-b483-e27e09ddc34d",
            "808a2de1-1aaa-4c25-a9b9-6612e8f29a38",
            "819e1fbf-8b7e-4f6d-811f-693534916a8b",
            "837ab141-399e-4c1f-9abc-bace40296bac",
            "a0a4f044-b040-410d-8ead-4de0446aec7e",
            "d3588630-ad8e-49df-bbd7-3167f7efb246",
            "zzz4f044-b040-410d-8ead-4de0446aec7e"
        ]
        product_id = random.choice(product_ids)
        self.client.get(f"/detail.html?id={product_id}", name="Product Detail")
    
    @task(3)  # Weight 3: Add to cart
    def add_to_cart(self):
        """Add item to shopping cart"""
        product_ids = [
            "03fef6ac-1896-4ce8-bd69-b798f85c6e0b",
            "510a0d7e-8e83-4193-b483-e27e09ddc34d",
            "808a2de1-1aaa-4c25-a9b9-6612e8f29a38"
        ]
        
        # View product first
        product_id = random.choice(product_ids)
        self.client.get(f"/detail.html?id={product_id}", name="View Before Add")
        
        # Add to cart (POST request)
        self.client.post(
            "/cart",
            json={"id": product_id, "quantity": 1},
            name="Add to Cart"
        )
        self.cart_items.append(product_id)
    
    @task(2)  # Weight 2: View cart
    def view_cart(self):
        """View shopping cart"""
        self.client.get("/basket.html", name="View Cart")
    
    @task(1)  # Weight 1: Checkout (least common - conversion funnel)
    def checkout(self):
        """Attempt checkout flow"""
        if not self.cart_items:
            # Add item if cart is empty
            self.add_to_cart()
        
        # View cart
        self.client.get("/basket.html", name="Cart Before Checkout")
        
        # Note: Full checkout requires authentication
        # For load testing, we just simulate the journey
        # Real checkout would need login + payment steps
    
    @task(4)  # Weight 4: Search/filter behavior
    def browse_with_parameters(self):
        """Browse with filters and sorting"""
        sort_options = ["price", "name", "newest"]
        sort = random.choice(sort_options)
        
        self.client.get(f"/catalogue?sort={sort}", name="Filtered Browse")
        
        # Simulate pagination
        page = random.randint(1, 3)
        self.client.get(f"/catalogue?page={page}", name="Pagination")


class IntensiveBrowser(HttpUser):
    """
    Power user who browses intensively
    Represents ~20% of users who generate more traffic
    """
    wait_time = between(0.5, 2)  # Faster browsing
    
    @task(15)
    def rapid_browse(self):
        """Rapidly browse multiple pages"""
        self.client.get("/")
        self.client.get("/catalogue")
        self.client.get("/category.html")
    
    @task(5)
    def view_multiple_products(self):
        """View several products in succession"""
        product_ids = [
            "03fef6ac-1896-4ce8-bd69-b798f85c6e0b",
            "510a0d7e-8e83-4193-b483-e27e09ddc34d",
            "808a2de1-1aaa-4c25-a9b9-6612e8f29a38",
            "819e1fbf-8b7e-4f6d-811f-693534916a8b"
        ]
        
        for product_id in random.sample(product_ids, 3):
            self.client.get(f"/detail.html?id={product_id}", name="Quick View")


# Usage examples:
#
# Basic load test (headless):
#   locust -f locustfile.py --host http://192.168.49.2:31987 \
#          --users 10 --spawn-rate 2 --run-time 5m --headless
#
# With web UI for monitoring:
#   locust -f locustfile.py --host http://192.168.49.2:31987 \
#          --web-host 0.0.0.0 --web-port 8089
#   Then open http://localhost:8089
#
# Specific user class:
#   locust -f locustfile.py --host http://192.168.49.2:31987 \
#          SockShopUser --users 50 --spawn-rate 10
#
# Realistic baseline (matches your traffic-generator.py):
#   locust -f locustfile.py --host http://192.168.49.2:31987 \
#          --users 10 --spawn-rate 2 --headless --run-time 10m
#   (Generates ~50-100 req/s depending on think time)
