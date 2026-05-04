#!/usr/bin/env python3
"""
Statistical Feature Extractor for Istio Metrics
Extracts CICDDoS2019-compatible statistical features from Istio/Prometheus metrics
Focuses on distribution-invariant features that transfer across domains
"""

import requests
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from collections import deque
from typing import Dict, List, Optional, Tuple
from loguru import logger
import yaml
from pathlib import Path


class IstioStatisticalFeatureExtractor:
    """
    Extract statistical features from Istio metrics that align with CICDDoS2019 feature space.
    Focus on rate statistics, distribution characteristics, and temporal patterns.
    """
    
    def __init__(self, prometheus_url: str, config_path: str = "config.yaml"):
        self.prometheus_url = prometheus_url
        
        # Load configuration
        with open(config_path, 'r') as f:
            self.config = yaml.safe_load(f)
        
        # Feature extraction windows
        self.windows = self.config['features']['windows']
        
        # Metric history for temporal features
        self.metric_history = {window: deque(maxlen=100) for window in self.windows}
        
        logger.info(f"Initialized feature extractor with windows: {self.windows}s")
    
    def query_prometheus(self, query: str, timeout: int = 10) -> float:
        """Execute Prometheus query and return scalar value"""
        try:
            response = requests.post(
                f"{self.prometheus_url}/api/v1/query",
                data={'query': query},
                timeout=timeout
            )
            data = response.json()
            
            if data['status'] == 'success' and data['data']['result']:
                value = float(data['data']['result'][0]['value'][1])
                # Handle NaN/Inf
                if np.isnan(value) or np.isinf(value):
                    return 0.0
                return value
            return 0.0
        except Exception as e:
            logger.debug(f"Query failed for query '{query[:100]}...': {e}")
            return 0.0
    
    def query_histogram_quantile(self, metric: str, quantile: float, window: int) -> float:
        """Query histogram quantile from Prometheus"""
        query = f'histogram_quantile({quantile}, sum(rate({metric}[{window}s])) by (le))'
        return self.query_prometheus(query)
    
    def collect_raw_metrics(self, window: int = 30) -> Dict[str, float]:
        """
        Collect raw Istio metrics for a given time window
        """
        metrics = {}
        
        # Request metrics
        metrics['request_rate'] = self.query_prometheus(
            f'sum(rate(istio_requests_total{{destination_service_namespace="sock-shop"}}[{window}s]))'
        )
        metrics['request_count'] = self.query_prometheus(
            f'sum(increase(istio_requests_total{{destination_service_namespace="sock-shop"}}[{window}s]))'
        )
        
        # Request/Response sizes
        metrics['request_bytes_rate'] = self.query_prometheus(
            f'sum(rate(istio_request_bytes_sum{{destination_service_namespace="sock-shop"}}[{window}s]))'
        )
        metrics['response_bytes_rate'] = self.query_prometheus(
            f'sum(rate(istio_response_bytes_sum{{destination_service_namespace="sock-shop"}}[{window}s]))'
        )
        metrics['avg_request_size'] = self.query_prometheus(
            f'sum(rate(istio_request_bytes_sum{{destination_service_namespace="sock-shop"}}[{window}s])) / '
            f'sum(rate(istio_request_bytes_count{{destination_service_namespace="sock-shop"}}[{window}s]))'
        )
        metrics['avg_response_size'] = self.query_prometheus(
            f'sum(rate(istio_response_bytes_sum{{destination_service_namespace="sock-shop"}}[{window}s])) / '
            f'sum(rate(istio_response_bytes_count{{destination_service_namespace="sock-shop"}}[{window}s]))'
        )
        
        # Latency percentiles
        metrics['latency_p50'] = self.query_histogram_quantile(
            'istio_request_duration_milliseconds_bucket', 0.50, window
        )
        metrics['latency_p95'] = self.query_histogram_quantile(
            'istio_request_duration_milliseconds_bucket', 0.95, window
        )
        metrics['latency_p99'] = self.query_histogram_quantile(
            'istio_request_duration_milliseconds_bucket', 0.99, window
        )
        
        # Error rates
        metrics['error_5xx_rate'] = self.query_prometheus(
            f'sum(rate(istio_requests_total{{destination_service_namespace="sock-shop",response_code=~"5.."}}[{window}s]))'
        )
        metrics['error_4xx_rate'] = self.query_prometheus(
            f'sum(rate(istio_requests_total{{destination_service_namespace="sock-shop",response_code=~"4.."}}[{window}s]))'
        )
        metrics['total_error_rate'] = metrics['error_5xx_rate'] + metrics['error_4xx_rate']
        
        # TCP metrics
        metrics['tcp_sent_bytes'] = self.query_prometheus(
            f'sum(rate(istio_tcp_sent_bytes_total{{destination_service_namespace="sock-shop"}}[{window}s]))'
        )
        metrics['tcp_received_bytes'] = self.query_prometheus(
            f'sum(rate(istio_tcp_received_bytes_total{{destination_service_namespace="sock-shop"}}[{window}s]))'
        )
        metrics['tcp_connections_opened'] = self.query_prometheus(
            f'sum(rate(istio_tcp_connections_opened_total{{destination_service_namespace="sock-shop"}}[{window}s]))'
        )
        metrics['tcp_connections_closed'] = self.query_prometheus(
            f'sum(rate(istio_tcp_connections_closed_total{{destination_service_namespace="sock-shop"}}[{window}s]))'
        )
        
        return metrics
    
    def extract_rate_features(self, metrics: Dict[str, float], window: int) -> Dict[str, float]:
        """
        Extract rate-based features
        Maps to CICDDoS: Flow Bytes/s, Flow Packets/s, Fwd/Bwd Packets/s
        """
        features = {}
        
        # Request rate statistics
        features[f'request_rate_w{window}'] = metrics['request_rate']
        features[f'packet_rate_w{window}'] = metrics['request_rate'] * 2  # Req + Resp
        
        # Byte rate statistics
        features[f'byte_rate_fwd_w{window}'] = metrics['request_bytes_rate']
        features[f'byte_rate_bwd_w{window}'] = metrics['response_bytes_rate']
        features[f'byte_rate_total_w{window}'] = (
            metrics['request_bytes_rate'] + metrics['response_bytes_rate']
        )
        
        # Connection rate
        features[f'connection_rate_w{window}'] = metrics['tcp_connections_opened']
        
        return features
    
    def extract_latency_features(self, metrics: Dict[str, float], window: int) -> Dict[str, float]:
        """
        Extract latency distribution features
        Maps to CICDDoS: Flow IAT Mean/Std, Active Time
        """
        features = {}
        
        # Latency percentiles (milliseconds)
        features[f'latency_p50_w{window}'] = metrics['latency_p50']
        features[f'latency_p95_w{window}'] = metrics['latency_p95']
        features[f'latency_p99_w{window}'] = metrics['latency_p99']
        
        # Latency spread (approximates std)
        features[f'latency_spread_w{window}'] = metrics['latency_p99'] - metrics['latency_p50']
        
        # Inter-arrival time (inverse of rate, in microseconds)
        iat_mean = (1.0 / max(metrics['request_rate'], 0.001)) * 1_000_000
        features[f'iat_mean_w{window}'] = iat_mean
        features[f'iat_std_w{window}'] = iat_mean * 0.4  # Approximate std as 40% of mean
        
        return features
    
    def extract_size_features(self, metrics: Dict[str, float], window: int) -> Dict[str, float]:
        """
        Extract packet/message size features
        Maps to CICDDoS: Packet Length Mean/Std/Min/Max
        """
        features = {}
        
        # Request size statistics
        features[f'request_size_mean_w{window}'] = metrics['avg_request_size']
        features[f'request_size_std_w{window}'] = metrics['avg_request_size'] * 0.3  # Approximate
        
        # Response size statistics
        features[f'response_size_mean_w{window}'] = metrics['avg_response_size']
        features[f'response_size_std_w{window}'] = metrics['avg_response_size'] * 0.3
        
        # Size ratio (asymmetry indicator)
        if metrics['avg_request_size'] > 0:
            features[f'size_ratio_w{window}'] = (
                metrics['avg_response_size'] / metrics['avg_request_size']
            )
        else:
            features[f'size_ratio_w{window}'] = 0.0
        
        return features
    
    def extract_error_features(self, metrics: Dict[str, float], window: int) -> Dict[str, float]:
        """
        Extract error rate features
        Maps to CICDDoS: Error indicators, flag anomalies
        """
        features = {}
        
        # Error rates
        features[f'error_rate_5xx_w{window}'] = metrics['error_5xx_rate']
        features[f'error_rate_4xx_w{window}'] = metrics['error_4xx_rate']
        features[f'error_rate_total_w{window}'] = metrics['total_error_rate']
        
        # Error ratio (errors / total requests)
        if metrics['request_rate'] > 0:
            features[f'error_ratio_w{window}'] = (
                metrics['total_error_rate'] / metrics['request_rate']
            )
        else:
            features[f'error_ratio_w{window}'] = 0.0
        
        return features
    
    def extract_flow_features(self, metrics: Dict[str, float], window: int) -> Dict[str, float]:
        """
        Extract flow-level features
        Maps to CICDDoS: Flow Duration, Active/Idle times
        """
        features = {}
        
        # Flow duration (use window as approximation)
        features[f'flow_duration_w{window}'] = window * 1_000_000  # microseconds
        
        # Connection metrics
        features[f'active_connections_w{window}'] = (
            metrics['tcp_connections_opened'] - metrics['tcp_connections_closed']
        )
        
        # Connection lifetime estimate
        if metrics['tcp_connections_closed'] > 0:
            features[f'connection_lifetime_w{window}'] = (
                window / max(metrics['tcp_connections_closed'], 0.001)
            )
        else:
            features[f'connection_lifetime_w{window}'] = window
        
        return features
    
    def extract_temporal_features(self, current_metrics: Dict[str, float], window: int) -> Dict[str, float]:
        """
        Extract temporal change features (deltas, trends)
        """
        features = {}
        
        # Store current metrics in history
        self.metric_history[window].append(current_metrics)
        
        if len(self.metric_history[window]) >= 2:
            prev_metrics = self.metric_history[window][-2]
            
            # Rate changes
            features[f'request_rate_delta_w{window}'] = (
                current_metrics['request_rate'] - prev_metrics['request_rate']
            )
            features[f'latency_delta_w{window}'] = (
                current_metrics['latency_p95'] - prev_metrics['latency_p95']
            )
            features[f'error_rate_delta_w{window}'] = (
                current_metrics['total_error_rate'] - prev_metrics['total_error_rate']
            )
        else:
            features[f'request_rate_delta_w{window}'] = 0.0
            features[f'latency_delta_w{window}'] = 0.0
            features[f'error_rate_delta_w{window}'] = 0.0
        
        # Compute variance over history window
        if len(self.metric_history[window]) >= 5:
            history_df = pd.DataFrame(list(self.metric_history[window]))
            features[f'request_rate_variance_w{window}'] = history_df['request_rate'].var()
            features[f'latency_variance_w{window}'] = history_df['latency_p95'].var()
        else:
            features[f'request_rate_variance_w{window}'] = 0.0
            features[f'latency_variance_w{window}'] = 0.0
        
        return features
    
    def extract_all_features(self) -> Dict[str, float]:
        """
        Extract complete feature vector from Istio metrics
        Returns ~35 features per window × number of windows
        """
        all_features = {}
        
        for window in self.windows:
            # Collect raw metrics for this window
            metrics = self.collect_raw_metrics(window)
            
            # Extract feature groups
            rate_features = self.extract_rate_features(metrics, window)
            latency_features = self.extract_latency_features(metrics, window)
            size_features = self.extract_size_features(metrics, window)
            error_features = self.extract_error_features(metrics, window)
            flow_features = self.extract_flow_features(metrics, window)
            temporal_features = self.extract_temporal_features(metrics, window)
            
            # Merge all features
            all_features.update(rate_features)
            all_features.update(latency_features)
            all_features.update(size_features)
            all_features.update(error_features)
            all_features.update(flow_features)
            all_features.update(temporal_features)
        
        logger.debug(f"Extracted {len(all_features)} features")
        return all_features
    
    def get_feature_vector(self) -> np.ndarray:
        """Get feature vector as numpy array"""
        features = self.extract_all_features()
        return np.array(list(features.values()))
    
    def get_feature_names(self) -> List[str]:
        """Get ordered list of feature names"""
        features = self.extract_all_features()
        return list(features.keys())


if __name__ == "__main__":
    # Test feature extraction
    logger.info("Testing feature extraction...")
    
    extractor = IstioStatisticalFeatureExtractor(
        prometheus_url="http://localhost:9090"
    )
    
    features = extractor.extract_all_features()
    print(f"\nExtracted {len(features)} features:")
    for name, value in list(features.items())[:10]:
        print(f"  {name}: {value:.4f}")
    print("  ...")
