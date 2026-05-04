#!/usr/bin/env python3
import numpy as np
import pandas as pd
import requests
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import RobustScaler
from pathlib import Path
import joblib
from loguru import logger
import shap


class PracticalDetector:
    # Universal statistical features that apply to both datasets
    UNIVERSAL_FEATURES = [
        'request_rate_mean',
        'request_rate_std',
        'latency_p50',
        'latency_p95',
        'latency_p99', 
        'error_rate',
        'byte_rate_mean',
        'packet_size_mean',
        'connection_rate',
        'traffic_burstiness'  # std/mean ratio
    ]
    
    def __init__(self, prometheus_url='http://localhost:9090'):
        self.prometheus_url = prometheus_url
        self.model = None
        self.scaler = None
        self.explainer = None
        
    def extract_from_istio(self) -> np.ndarray:
        """Extract universal features from Istio metrics"""
        features = {}
        
        # Query Prometheus
        def query(q):
            try:
                resp = requests.post(f"{self.prometheus_url}/api/v1/query", data={'query': q}, timeout=5)
                data = resp.json()
                if data['status'] == 'success' and data['data']['result']:
                    return float(data['data']['result'][0]['value'][1])
            except:
                pass
            return 0.0
        
        # Request rate
        rate_30s = query('sum(rate(istio_requests_total{destination_service_namespace="sock-shop"}[30s]))')
        rate_10s = query('sum(rate(istio_requests_total{destination_service_namespace="sock-shop"}[10s]))')
        features['request_rate_mean'] = rate_30s
        features['request_rate_std'] = abs(rate_30s - rate_10s) 
        
        # Latency
        features['latency_p50'] = query('histogram_quantile(0.50, sum(rate(istio_request_duration_milliseconds_bucket{destination_service_namespace="sock-shop"}[30s])) by (le))')
        features['latency_p95'] = query('histogram_quantile(0.95, sum(rate(istio_request_duration_milliseconds_bucket{destination_service_namespace="sock-shop"}[30s])) by (le))')
        features['latency_p99'] = query('histogram_quantile(0.99, sum(rate(istio_request_duration_milliseconds_bucket{destination_service_namespace="sock-shop"}[30s])) by (le))')
        
        # Error rate
        error_rate = query('sum(rate(istio_requests_total{destination_service_namespace="sock-shop",response_code=~"5.."}[30s]))')
        features['error_rate'] = error_rate / max(rate_30s, 0.001)
        
        # Byte rate
        features['byte_rate_mean'] = query('sum(rate(istio_response_bytes_sum{destination_service_namespace="sock-shop"}[30s]))')
        
        # Packet size
        resp_size = query('sum(rate(istio_response_bytes_sum{destination_service_namespace="sock-shop"}[30s])) / sum(rate(istio_response_bytes_count{destination_service_namespace="sock-shop"}[30s]))')
        features['packet_size_mean'] = resp_size
        
        # Connection rate
        features['connection_rate'] = query('sum(rate(istio_tcp_connections_opened_total{destination_service_namespace="sock-shop"}[30s]))')
        
        # Burstiness
        features['traffic_burstiness'] = features['request_rate_std'] / max(features['request_rate_mean'], 0.001)
        
        return np.array([features[k] for k in self.UNIVERSAL_FEATURES]).reshape(1, -1)
    
    def extract_from_cicddos(self, df: pd.DataFrame) -> np.ndarray:
        """Extract universal features from CICDDoS2019 dataframe"""
        features_list = []
        
        # Process in chunks to get distributions
        for _,  row in df.iterrows():
            feats = {}
            
            # Map CICDDoS to universal features
            feats['request_rate_mean'] = row.get('Flow Packets/s', 0)
            feats['request_rate_std'] = row.get('Flow IAT Std', 0) / 1000  # Convert to comparable scale
            feats['latency_p50'] = row.get('Flow IAT Mean', 0) / 1000
            feats['latency_p95'] = row.get('Fwd IAT Max', 0) / 1000
            feats['latency_p99'] = row.get('Bwd IAT Max', 0) / 1000
            feats['error_rate'] = 0.0  # CICDDoS doesn't have error rate
            feats['byte_rate_mean'] = row.get('Flow Bytes/s', 0)
            feats['packet_size_mean'] = row.get('Average Packet Size', row.get('Packet Length Mean', 0))
            feats['connection_rate'] = row.get('Flow Packets/s', 0) / 10  # Rough approximation
            
            # Burstiness
            flow_rate = row.get('Flow Packets/s', 0.001)
            flow_std = row.get('Flow IAT Std', 0)
            feats['traffic_burstiness'] = (flow_std / 1000) / max(flow_rate, 0.001)
            
            features_list.append([feats[k] for k in self.UNIVERSAL_FEATURES])
        
        return np.array(features_list)
    
    def train(self, cicddos_path: str):
        """Train on CICDDoS2019 normal traffic"""
        logger.info(f"Training on {cicddos_path}")
        
        # Load dataset
        all_data = []
        for pf in Path(cicddos_path).glob("*.parquet"):
            df = pd.read_parquet(pf)
            all_data.append(df)
        
        combined = pd.concat(all_data, ignore_index=True)
        combined.columns = combined.columns.str.strip()
        
        label_col = 'Label' if 'Label' in combined.columns else ' Label'
        normal = combined[combined[label_col].str.upper() == 'BENIGN']
        
        if len(normal) > 50000:
            normal = normal.sample(n=50000, random_state=42)
        
        logger.info(f"Extracting features from {len(normal)} normal samples")
        X = self.extract_from_cicddos(normal)
        
        # Remove inf/nan
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
        
        # Scale
        self.scaler = RobustScaler()
        X_scaled = self.scaler.fit_transform(X)
        
        # Train
        logger.info("Training Isolation Forest")
        self.model = IsolationForest(
            n_estimators=200,
            contamination=0.10,
            max_samples=256,
            random_state=42,
            n_jobs=-1
        )
        self.model.fit(X_scaled)
        
        # Initialize SHAP explainer
        logger.info("Initializing SHAP explainer...")
        self.explainer = shap.TreeExplainer(self.model)
        
        logger.info("Training complete")
    
    def detect(self, explain=False) -> tuple:
        """Detect anomaly in current Istio traffic"""
        X = self.extract_from_istio()
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
        X_scaled = self.scaler.transform(X)
        
        score = self.model.decision_function(X_scaled)[0]
        is_anomaly = self.model.predict(X_scaled)[0] == -1
        
        explanation = None
        if explain and is_anomaly and self.explainer:
            explanation = self._explain(X_scaled)
        
        return is_anomaly, score, explanation
    
    def _explain(self, X_scaled: np.ndarray) -> dict:
        """Generate SHAP explanation for detection"""
        try:
            shap_values = self.explainer.shap_values(X_scaled)
            
            # Handle multi-dimensional SHAP values
            if len(shap_values.shape) > 1:
                shap_values = shap_values[0]
            
            # Get feature contributions
            feature_importance = {}
            for i, feature_name in enumerate(self.UNIVERSAL_FEATURES):
                feature_importance[feature_name] = float(shap_values[i])
            
            # Sort by absolute contribution
            sorted_features = sorted(
                feature_importance.items(),
                key=lambda x: abs(x[1]),
                reverse=True
            )
            
            return {
                'top_features': sorted_features[:5],  # Top 5 contributors
                'all_features': feature_importance
            }
        except Exception as e:
            logger.error(f"Failed to generate explanation: {e}")
            return None
    
    def save(self, path: str):
        """Save model"""
        joblib.dump({
            'model': self.model,
            'scaler': self.scaler,
            'explainer': self.explainer
        }, path)
        logger.info(f"Saved to {path}")
    
    def load(self, path: str):
        """Load model"""
        data = joblib.load(path)
        self.model = data['model']
        self.scaler = data['scaler']
        self.explainer = data.get('explainer')  # May not exist in old models
        logger.info(f"Loaded from {path}")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage:")
        print("  Train: python practical_detector.py train /path/to/cicddos2019")
        print("  Detect: python practical_detector.py detect [--explain]")
        sys.exit(1)
    
    command = sys.argv[1]
    detector = PracticalDetector()
    
    if command == "train":
        dataset_path = sys.argv[2]
        detector.train(dataset_path)
        detector.save("models/practical_detector.pkl")
    
    elif command == "detect":
        explain = "--explain" in sys.argv
        detector.load("models/practical_detector.pkl")
        is_anomaly, score, explanation = detector.detect(explain=explain)
        
        if is_anomaly:
            print(f"🚨 ATTACK DETECTED! Anomaly score: {score:.3f}")
            if explanation:
                print("\n🔍 SHAP Explanation - Top Contributing Features:")
                for feature, contribution in explanation['top_features']:
                    direction = "increases" if contribution > 0 else "decreases"
                    print(f"  • {feature}: {contribution:.4f} ({direction} anomaly score)")
        else:
            print(f"✅ Normal traffic. Score: {score:.3f}")
