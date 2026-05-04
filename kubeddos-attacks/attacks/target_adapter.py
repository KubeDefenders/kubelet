"""
Attack Target Adapter
Provides unified interface for attacks to work with any target application.
Phase 4: Target Abstraction Layer for Attacks Component
"""

import yaml
import json
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AttackEndpoint:
    """Endpoint configuration for attack targeting"""
    path: str
    method: str
    weight: float = 1.0  # Selection probability weight
    resource_cost: str = "low"  # low, medium, high, critical
    auth_required: bool = False
    expected_latency_ms: int = 100
    category: str = "general"  # frontend, backend, database, cache, api


@dataclass
class AttackProfile:
    """Traffic profile for attack simulation"""
    name: str
    requests_per_second: int
    burst_size: int
    connection_timeout: float
    read_timeout: float
    concurrent_connections: int
    user_agent_rotation: bool = True


class AttackTargetAdapter:
    """
    Adapter for targeting any application with DDoS attacks.
    Provides endpoint discovery, prioritization, and attack surface mapping.
    """
    
    def __init__(self, adapter_config_path: str = None, base_url: str = None):
        """
        Initialize attack target adapter.
        
        Args:
            adapter_config_path: Path to target-adapter YAML config
            base_url: Base URL for target (fallback if no config)
        """
        self.base_url = base_url
        self.endpoints: List[AttackEndpoint] = []
        self.decoy_endpoints: List[AttackEndpoint] = []
        self.target_endpoints: List[AttackEndpoint] = []
        self.config = {}
        
        if adapter_config_path:
            self.load_config(adapter_config_path)
        elif base_url:
            # Don't init default config here - wait for load_discovered_endpoints
            pass
    
    def load_config(self, config_path: str):
        """Load target configuration from YAML"""
        path = Path(config_path)
        if not path.exists():
            raise FileNotFoundError(f"Adapter config not found: {config_path}")
        
        with open(path) as f:
            self.config = yaml.safe_load(f)
        
        self.base_url = self.config.get('target_info', {}).get('base_url', self.base_url)
        self._parse_endpoints()
    
    def load_discovered_endpoints(self, discovery_file: str):
        """
        Load endpoints from endpoint-discovery.py output.
        Enriches adapter with actual discovered endpoints.
        """
        path = Path(discovery_file)
        if not path.exists():
            raise FileNotFoundError(f"Discovery file not found: {discovery_file}")
        
        with open(path) as f:
            data = json.load(f)
        
        # Update base URL if present
        if 'base_url' in data:
            self.base_url = data['base_url']
        
        # Parse discovered endpoints
        discovered_endpoints = []
        for endpoint_list_key in ['api_endpoints', 'discovered_urls']:
            for endpoint_str in data.get(endpoint_list_key, []):
                # Extract path from full URL if present
                if endpoint_str.startswith('http'):
                    # Remove base_url to get just the path
                    path = endpoint_str.replace(self.base_url, '')
                else:
                    path = endpoint_str
                
                # Create endpoint object with default values
                endpoint = AttackEndpoint(
                    path=path,
                    method='GET',
                    weight=1.0,
                    resource_cost='medium',
                    category='discovered'
                )
                discovered_endpoints.append(endpoint)
        
        # Replace endpoints entirely with discovered ones (don't use defaults)
        self.endpoints = discovered_endpoints
        self._classify_endpoints()
    
    def _init_default_config(self, base_url: str):
        """Initialize with default generic configuration"""
        self.base_url = base_url
        
        # Generic endpoints that exist on most web apps
        default_endpoints = [
            AttackEndpoint("/", "GET", 1.0, "low", category="frontend"),
            AttackEndpoint("/index", "GET", 1.0, "low", category="frontend"),
            AttackEndpoint("/api", "GET", 1.0, "medium", category="api"),
            AttackEndpoint("/health", "GET", 0.5, "low", category="health"),
            AttackEndpoint("/metrics", "GET", 0.5, "low", category="monitoring"),
        ]
        
        self.endpoints = default_endpoints
        self._classify_endpoints()
    
    def _parse_endpoints(self):
        """Parse endpoints from config"""
        self.endpoints = []
        
        for service in self.config.get('services', []):
            for ep in service.get('endpoints', []):
                endpoint = AttackEndpoint(
                    path=ep['path'],
                    method=ep.get('method', 'GET'),
                    weight=ep.get('weight', 1.0),
                    resource_cost=ep.get('resource_cost', 'medium'),
                    auth_required=ep.get('auth_required', False),
                    expected_latency_ms=ep.get('expected_latency_ms', 100),
                    category=service.get('type', 'general')
                )
                self.endpoints.append(endpoint)
        
        self._classify_endpoints()
    
    def _classify_endpoints(self):
        """Classify endpoints into decoys and targets for crossfire attacks"""
        # Reset classifications
        self.decoy_endpoints = []
        self.target_endpoints = []
        
        # Crossfire strategy: High-resource endpoints are targets,
        # lower-resource endpoints are decoys
        for ep in self.endpoints:
            if ep.resource_cost in ['high', 'critical']:
                self.target_endpoints.append(ep)
            elif not ep.auth_required:  # Decoys should be publicly accessible
                self.decoy_endpoints.append(ep)
        
        # If no explicit classification, use heuristics
        if not self.decoy_endpoints and not self.target_endpoints:
            # Default: backend/api endpoints are targets, frontend are decoys
            for ep in self.endpoints:
                if ep.category in ['backend', 'api', 'database']:
                    self.target_endpoints.append(ep)
                else:
                    self.decoy_endpoints.append(ep)
    
    def get_decoy_endpoints(self, limit: int = 0) -> List[str]:
        """
        Get decoy endpoint URLs for crossfire attack.
        
        Args:
            limit: Maximum number of decoys to return (0 = all)
        
        Returns:
            List of full URLs for decoy endpoints
        """
        decoys = [f"{self.base_url}{ep.path}" for ep in self.decoy_endpoints]
        
        if limit > 0:
            return decoys[:limit]
        return decoys
    
    def get_target_endpoints(self) -> List[str]:
        """Get target endpoint URLs (for monitoring, not direct attack in crossfire)"""
        return [f"{self.base_url}{ep.path}" for ep in self.target_endpoints]
    
    def get_weighted_endpoint(self) -> str:
        """Get random endpoint with weight-based selection"""
        import random
        
        if not self.decoy_endpoints:
            raise ValueError("No decoy endpoints available")
        
        # Weight-based random selection
        weights = [ep.weight for ep in self.decoy_endpoints]
        selected = random.choices(self.decoy_endpoints, weights=weights, k=1)[0]
        return f"{self.base_url}{selected.path}"
    
    def get_attack_profile(self, profile_name: str = "moderate") -> AttackProfile:
        """
        Get predefined attack profile.
        
        Profiles:
        - stealth: Low rate, mimics normal traffic
        - moderate: Medium rate, noticeable but not extreme
        - aggressive: High rate, clear attack signature
        - extreme: Maximum rate, overwhelming traffic
        """
        profiles = {
            "stealth": AttackProfile(
                name="stealth",
                requests_per_second=5,
                burst_size=3,
                connection_timeout=5.0,
                read_timeout=10.0,
                concurrent_connections=10,
                user_agent_rotation=True
            ),
            "moderate": AttackProfile(
                name="moderate",
                requests_per_second=50,
                burst_size=10,
                connection_timeout=3.0,
                read_timeout=5.0,
                concurrent_connections=50,
                user_agent_rotation=True
            ),
            "aggressive": AttackProfile(
                name="aggressive",
                requests_per_second=200,
                burst_size=20,
                connection_timeout=2.0,
                read_timeout=3.0,
                concurrent_connections=100,
                user_agent_rotation=False
            ),
            "extreme": AttackProfile(
                name="extreme",
                requests_per_second=1000,
                burst_size=50,
                connection_timeout=1.0,
                read_timeout=2.0,
                concurrent_connections=500,
                user_agent_rotation=False
            )
        }
        
        return profiles.get(profile_name, profiles["moderate"])
    
    def get_normal_traffic_profile(self) -> Dict:
        """Get normal traffic baseline (for comparison)"""
        detection_config = self.config.get('detection', {})
        return detection_config.get('normal_traffic_profile', {
            'requests_per_second': 10,
            'avg_latency_ms': 100,
            'error_rate_percent': 1.0
        })
    
    def suggest_crossfire_strategy(self) -> Dict[str, any]:
        """
        Suggest crossfire attack strategy based on target configuration.
        
        Returns:
            Dictionary with attack recommendations
        """
        strategy = {
            'decoy_count': len(self.decoy_endpoints),
            'target_count': len(self.target_endpoints),
            'recommended_workers': min(len(self.decoy_endpoints) * 10, 100),
            'recommended_rate': 50,  # per worker
            'recommended_duration': 300,
            'decoy_distribution': 'weighted',  # or 'uniform'
            'attack_vector': 'application',  # or 'network' or 'hybrid'
        }
        
        # Adjust based on endpoint count
        if len(self.decoy_endpoints) < 5:
            strategy['recommended_workers'] = 20
            strategy['recommended_rate'] = 100
            strategy['attack_vector'] = 'hybrid'
        elif len(self.decoy_endpoints) > 20:
            strategy['recommended_workers'] = 200
            strategy['recommended_rate'] = 30
        
        return strategy
    
    def export_for_monitoring(self, output_path: str):
        """Export target configuration for monitoring/detection tools"""
        export_data = {
            'base_url': self.base_url,
            'total_endpoints': len(self.endpoints),
            'decoy_endpoints': [
                {'path': ep.path, 'category': ep.category, 'resource_cost': ep.resource_cost}
                for ep in self.decoy_endpoints
            ],
            'target_endpoints': [
                {'path': ep.path, 'category': ep.category, 'resource_cost': ep.resource_cost}
                for ep in self.target_endpoints
            ],
            'normal_profile': self.get_normal_traffic_profile(),
            'crossfire_strategy': self.suggest_crossfire_strategy()
        }
        
        with open(output_path, 'w') as f:
            json.dump(export_data, f, indent=2)
        
        print(f"✓ Exported target configuration to: {output_path}")


# Convenience function for quick adapter creation
def create_adapter(
    base_url: str = None,
    adapter_config: str = None,
    discovery_file: str = None
) -> AttackTargetAdapter:
    """
    Create attack target adapter with flexible input options.
    
    Priority: adapter_config > discovery_file > base_url
    """
    if adapter_config:
        adapter = AttackTargetAdapter(adapter_config_path=adapter_config)
    elif base_url:
        adapter = AttackTargetAdapter(base_url=base_url)
    else:
        raise ValueError("Must provide either adapter_config or base_url")
    
    # Enrich with discovered endpoints if available
    if discovery_file:
        adapter.load_discovered_endpoints(discovery_file)
    
    return adapter


if __name__ == "__main__":
    # Example usage
    import argparse
    
    parser = argparse.ArgumentParser(description="Attack Target Adapter")
    parser.add_argument('--base-url', required=True, help='Target base URL')
    parser.add_argument('--config', help='Path to target adapter YAML config')
    parser.add_argument('--discovery', help='Path to endpoint discovery JSON')
    parser.add_argument('--export', help='Export configuration to file')
    
    args = parser.parse_args()
    
    # Create adapter
    adapter = create_adapter(
        base_url=args.base_url,
        adapter_config=args.config,
        discovery_file=args.discovery
    )
    
    # Print summary
    print(f"\n{'='*60}")
    print(f"Attack Target Adapter")
    print(f"{'='*60}")
    print(f"Base URL: {adapter.base_url}")
    print(f"Total Endpoints: {len(adapter.endpoints)}")
    print(f"Decoy Endpoints: {len(adapter.decoy_endpoints)}")
    print(f"Target Endpoints: {len(adapter.target_endpoints)}")
    print(f"")
    
    strategy = adapter.suggest_crossfire_strategy()
    print(f"Recommended Crossfire Strategy:")
    for key, value in strategy.items():
        print(f"  {key}: {value}")
    print(f"{'='*60}\n")
    
    # Export if requested
    if args.export:
        adapter.export_for_monitoring(args.export)
