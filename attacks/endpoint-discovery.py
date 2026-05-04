#!/usr/bin/env python3
"""
Endpoint Discovery Tool for Crossfire DDoS Attack Preparation
Discovers and maps HTTP endpoints without prior knowledge of the target architecture.
"""

import argparse
import requests
import json
import re
import time
from urllib.parse import urljoin, urlparse, parse_qs
from bs4 import BeautifulSoup
from collections import defaultdict
import logging
from typing import Set, Dict, List
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class EndpointDiscovery:
    """Discovers HTTP endpoints through crawling and probing."""
    
    def __init__(self, base_url: str, max_depth: int = 3, timeout: int = 5):
        self.base_url = base_url.rstrip('/')
        self.max_depth = max_depth
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Security Research Bot)'
        })
        
        # Discovery results
        self.discovered_urls: Set[str] = set()
        self.api_endpoints: Set[str] = set()
        self.static_resources: Set[str] = set()
        self.forms: List[Dict] = []
        self.endpoint_profiles: Dict[str, Dict] = {}
        
        # Statistics
        self.stats = {
            'requests_made': 0,
            'successful_requests': 0,
            'failed_requests': 0,
            'discovery_time': 0
        }
    
    def is_valid_url(self, url: str) -> bool:
        """Check if URL belongs to target domain."""
        try:
            parsed = urlparse(url)
            base_parsed = urlparse(self.base_url)
            return parsed.netloc == base_parsed.netloc or not parsed.netloc
        except:
            return False
    
    def normalize_url(self, url: str) -> str:
        """Normalize and clean URL."""
        if not url:
            return None
        
        # Handle relative URLs
        if not url.startswith('http'):
            url = urljoin(self.base_url, url)
        
        # Remove fragments
        parsed = urlparse(url)
        url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        
        # Remove query parameters for now (we'll handle them separately)
        if parsed.query:
            url = f"{url}?{parsed.query}"
        
        return url
    
    def extract_links(self, html: str, current_url: str) -> Set[str]:
        """Extract all links from HTML content."""
        links = set()
        
        try:
            soup = BeautifulSoup(html, 'html.parser')
            
            # Extract href links
            for tag in soup.find_all(['a', 'link'], href=True):
                href = tag.get('href')
                normalized = self.normalize_url(href)
                if normalized and self.is_valid_url(normalized):
                    links.add(normalized)
            
            # Extract src attributes (images, scripts, etc.)
            for tag in soup.find_all(['img', 'script', 'iframe'], src=True):
                src = tag.get('src')
                normalized = self.normalize_url(src)
                if normalized and self.is_valid_url(normalized):
                    # Classify as static resource
                    if any(ext in src.lower() for ext in ['.js', '.css', '.png', '.jpg', '.gif', '.svg', '.ico']):
                        self.static_resources.add(normalized)
                    else:
                        links.add(normalized)
            
            # Extract forms
            for form in soup.find_all('form'):
                form_data = {
                    'action': self.normalize_url(form.get('action', current_url)),
                    'method': form.get('method', 'GET').upper(),
                    'inputs': []
                }
                for input_tag in form.find_all('input'):
                    form_data['inputs'].append({
                        'name': input_tag.get('name'),
                        'type': input_tag.get('type', 'text'),
                        'value': input_tag.get('value', '')
                    })
                self.forms.append(form_data)
                if form_data['action']:
                    links.add(form_data['action'])
            
            # Look for API patterns in scripts
            scripts = soup.find_all('script')
            for script in scripts:
                if script.string:
                    # Look for common API patterns
                    api_patterns = [
                        r'["\']/(api|v\d+)/[^"\']+["\']',
                        r'["\']/(catalogue|cart|order|user|payment|shipping)/[^"\']+["\']',
                        r'fetch\(["\']([^"\']+)["\']',
                        r'axios\.[a-z]+\(["\']([^"\']+)["\']'
                    ]
                    for pattern in api_patterns:
                        matches = re.findall(pattern, script.string)
                        for match in matches:
                            if isinstance(match, tuple):
                                match = match[0] if match[0].startswith('/') else match[1]
                            normalized = self.normalize_url(match)
                            if normalized and self.is_valid_url(normalized):
                                self.api_endpoints.add(normalized)
                                links.add(normalized)
        
        except Exception as e:
            logger.debug(f"Error extracting links: {e}")
        
        return links
    
    def profile_endpoint(self, url: str) -> Dict:
        """Profile an endpoint to gather characteristics."""
        profile = {
            'url': url,
            'methods': [],
            'status_codes': {},
            'avg_response_time': 0,
            'content_type': None,
            'content_length': 0,
            'requires_auth': False,
            'resource_intensive': False
        }
        
        # Try different HTTP methods
        methods_to_try = ['GET', 'HEAD', 'OPTIONS']
        response_times = []
        
        for method in methods_to_try:
            try:
                start_time = time.time()
                response = self.session.request(
                    method, 
                    url, 
                    timeout=self.timeout,
                    allow_redirects=False
                )
                response_time = time.time() - start_time
                response_times.append(response_time)
                
                self.stats['requests_made'] += 1
                self.stats['successful_requests'] += 1
                
                if response.status_code not in [405, 501]:  # Method not allowed
                    profile['methods'].append(method)
                
                profile['status_codes'][method] = response.status_code
                
                if method == 'GET':
                    profile['content_type'] = response.headers.get('Content-Type', '')
                    profile['content_length'] = len(response.content)
                    
                    # Check for authentication requirement
                    if response.status_code in [401, 403]:
                        profile['requires_auth'] = True
                    
                    # Mark as resource intensive if slow
                    if response_time > 1.0:
                        profile['resource_intensive'] = True
                
                # Respect rate limiting
                time.sleep(0.1)
                
            except requests.exceptions.Timeout:
                profile['resource_intensive'] = True
                self.stats['failed_requests'] += 1
            except Exception as e:
                logger.debug(f"Error profiling {url} with {method}: {e}")
                self.stats['failed_requests'] += 1
        
        if response_times:
            profile['avg_response_time'] = sum(response_times) / len(response_times)
        
        return profile
    
    def crawl(self, url: str, depth: int = 0, visited: Set[str] = None) -> None:
        """Recursively crawl and discover endpoints."""
        if visited is None:
            visited = set()
        
        if depth > self.max_depth or url in visited:
            return
        
        visited.add(url)
        logger.info(f"Crawling [{depth}/{self.max_depth}]: {url}")
        
        try:
            response = self.session.get(url, timeout=self.timeout)
            self.stats['requests_made'] += 1
            self.stats['successful_requests'] += 1
            
            self.discovered_urls.add(url)
            
            # Profile this endpoint
            self.endpoint_profiles[url] = self.profile_endpoint(url)
            
            # Only crawl HTML content
            content_type = response.headers.get('Content-Type', '')
            if 'text/html' in content_type:
                links = self.extract_links(response.text, url)
                
                # Recursively crawl discovered links
                for link in links:
                    if link not in visited:
                        self.crawl(link, depth + 1, visited)
            
            # Small delay to be polite
            time.sleep(0.2)
            
        except requests.exceptions.Timeout:
            logger.warning(f"Timeout accessing {url}")
            self.stats['failed_requests'] += 1
        except requests.exceptions.RequestException as e:
            logger.debug(f"Error crawling {url}: {e}")
            self.stats['failed_requests'] += 1
    
    def discover_common_paths(self) -> None:
        """Try common API and endpoint paths."""
        common_paths = [
            '/api', '/api/v1', '/api/v2',
            '/catalogue', '/catalogue/images', '/catalogue/size',
            '/cart', '/carts', '/basket',
            '/order', '/orders',
            '/customer', '/customers', '/user', '/users',
            '/payment', '/payments',
            '/shipping',
            '/tags', '/items', '/products',
            '/health', '/healthcheck', '/status',
            '/metrics', '/prometheus'
        ]
        
        logger.info("Probing common paths...")
        for path in common_paths:
            url = urljoin(self.base_url, path)
            try:
                response = self.session.get(url, timeout=self.timeout)
                self.stats['requests_made'] += 1
                
                if response.status_code < 400:
                    self.stats['successful_requests'] += 1
                    self.discovered_urls.add(url)
                    self.api_endpoints.add(url)
                    self.endpoint_profiles[url] = self.profile_endpoint(url)
                    logger.info(f"Found: {url} [{response.status_code}]")
                else:
                    self.stats['failed_requests'] += 1
                
                time.sleep(0.1)
            except:
                self.stats['failed_requests'] += 1
                pass
    
    def categorize_endpoints(self) -> Dict[str, List[str]]:
        """Categorize discovered endpoints by type."""
        categories = {
            'high_value_targets': [],  # Resource intensive endpoints
            'api_endpoints': [],
            'static_resources': [],
            'forms': [],
            'requires_auth': [],
            'fast_endpoints': [],
            'slow_endpoints': []
        }
        
        for url, profile in self.endpoint_profiles.items():
            # High value targets (resource intensive)
            if profile['resource_intensive']:
                categories['high_value_targets'].append(url)
                categories['slow_endpoints'].append(url)
            elif profile['avg_response_time'] > 0 and profile['avg_response_time'] < 0.1:
                categories['fast_endpoints'].append(url)
            
            # API endpoints
            if any(pattern in url.lower() for pattern in ['/api', '/v1', '/v2', 'json']):
                categories['api_endpoints'].append(url)
            
            # Authentication required
            if profile['requires_auth']:
                categories['requires_auth'].append(url)
        
        categories['static_resources'] = list(self.static_resources)
        categories['forms'] = [f['action'] for f in self.forms]
        
        return categories
    
    def generate_decoy_links(self, count: int = 100) -> List[str]:
        """Generate decoy link targets based on discovered structure."""
        decoy_links = []
        
        # Use discovered endpoints as templates
        base_endpoints = list(self.discovered_urls)[:10]  # Take first 10
        
        for i in range(count):
            if base_endpoints:
                # Pick a random base endpoint and modify it
                import random
                base = random.choice(base_endpoints)
                parsed = urlparse(base)
                
                # Generate variations
                variations = [
                    f"{base}?id={i}",
                    f"{base}/{i}",
                    f"{base}?page={i}",
                    f"{parsed.scheme}://{parsed.netloc}/decoy-{i}",
                ]
                decoy_links.extend(variations)
        
        return decoy_links[:count]
    
    def export_results(self, output_file: str) -> None:
        """Export discovery results to JSON."""
        results = {
            'base_url': self.base_url,
            'discovery_time': datetime.now().isoformat(),
            'statistics': self.stats,
            'discovered_urls': list(self.discovered_urls),
            'api_endpoints': list(self.api_endpoints),
            'static_resources': list(self.static_resources),
            'forms': self.forms,
            'endpoint_profiles': self.endpoint_profiles,
            'categorized': self.categorize_endpoints(),
            'recommended_targets': self.get_recommended_targets()
        }
        
        with open(output_file, 'w') as f:
            json.dump(results, f, indent=2)
        
        logger.info(f"Results exported to {output_file}")
    
    def get_recommended_targets(self) -> Dict[str, List[str]]:
        """Get recommended targets for different attack types."""
        categories = self.categorize_endpoints()
        
        recommendations = {
            'primary_targets': categories['high_value_targets'][:5],  # Top 5 resource-intensive
            'decoy_targets': categories['fast_endpoints'][:20],  # Fast endpoints for decoys
            'api_targets': categories['api_endpoints'][:10],
            'all_targets': list(self.discovered_urls)
        }
        
        return recommendations
    
    def print_summary(self) -> None:
        """Print discovery summary."""
        categories = self.categorize_endpoints()
        
        print("\n" + "="*70)
        print("ENDPOINT DISCOVERY SUMMARY")
        print("="*70)
        print(f"Base URL: {self.base_url}")
        print(f"Discovery Time: {self.stats['discovery_time']:.2f}s")
        print(f"\nStatistics:")
        print(f"  Total Requests: {self.stats['requests_made']}")
        print(f"  Successful: {self.stats['successful_requests']}")
        print(f"  Failed: {self.stats['failed_requests']}")
        print(f"\nDiscovered Endpoints:")
        print(f"  Total URLs: {len(self.discovered_urls)}")
        print(f"  API Endpoints: {len(categories['api_endpoints'])}")
        print(f"  Static Resources: {len(categories['static_resources'])}")
        print(f"  Forms: {len(categories['forms'])}")
        print(f"\nTarget Classification:")
        print(f"  High-Value Targets (slow/resource-intensive): {len(categories['high_value_targets'])}")
        print(f"  Fast Endpoints (good for decoys): {len(categories['fast_endpoints'])}")
        print(f"  Auth Required: {len(categories['requires_auth'])}")
        
        print(f"\n{'='*70}")
        print("RECOMMENDED ATTACK TARGETS")
        print("="*70)
        
        recommendations = self.get_recommended_targets()
        
        print("\nPrimary Targets (resource-intensive):")
        for i, url in enumerate(recommendations['primary_targets'][:5], 1):
            profile = self.endpoint_profiles.get(url, {})
            rt = profile.get('avg_response_time', 0)
            print(f"  {i}. {url} (avg response: {rt:.3f}s)")
        
        print("\nDecoy Targets (fast response):")
        for i, url in enumerate(recommendations['decoy_targets'][:5], 1):
            profile = self.endpoint_profiles.get(url, {})
            rt = profile.get('avg_response_time', 0)
            print(f"  {i}. {url} (avg response: {rt:.3f}s)")
        
        print("\n" + "="*70 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description='Discover HTTP endpoints for Crossfire DDoS attack preparation'
    )
    parser.add_argument(
        '--target',
        required=True,
        help='Target URL (e.g., http://192.168.49.2:30001)'
    )
    parser.add_argument(
        '--max-depth',
        type=int,
        default=3,
        help='Maximum crawl depth (default: 3)'
    )
    parser.add_argument(
        '--timeout',
        type=int,
        default=5,
        help='Request timeout in seconds (default: 5)'
    )
    parser.add_argument(
        '--output',
        default='discovered-endpoints.json',
        help='Output file for results (default: discovered-endpoints.json)'
    )
    parser.add_argument(
        '--no-crawl',
        action='store_true',
        help='Skip crawling, only probe common paths'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    print(f"\n{'='*70}")
    print("CROSSFIRE DDOS - ENDPOINT DISCOVERY")
    print("="*70)
    print(f"Target: {args.target}")
    print(f"Max Depth: {args.max_depth}")
    print(f"Output: {args.output}")
    print("="*70 + "\n")
    
    discovery = EndpointDiscovery(
        base_url=args.target,
        max_depth=args.max_depth,
        timeout=args.timeout
    )
    
    start_time = time.time()
    
    try:
        # Always probe common paths
        discovery.discover_common_paths()
        
        # Optionally crawl
        if not args.no_crawl:
            logger.info("Starting web crawl...")
            discovery.crawl(args.target)
        
        discovery.stats['discovery_time'] = time.time() - start_time
        
        # Print summary
        discovery.print_summary()
        
        # Export results
        discovery.export_results(args.output)
        
        print(f"\n✅ Discovery complete! Results saved to {args.output}")
        print(f"   Use this file with attack simulations: --targets-file {args.output}\n")
        
    except KeyboardInterrupt:
        print("\n\n⚠️  Discovery interrupted by user")
        discovery.stats['discovery_time'] = time.time() - start_time
        discovery.export_results(args.output)
        print(f"   Partial results saved to {args.output}\n")
    except Exception as e:
        logger.error(f"Discovery failed: {e}")
        raise


if __name__ == '__main__':
    main()
