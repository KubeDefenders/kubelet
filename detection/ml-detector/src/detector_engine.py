#!/usr/bin/env python3
"""
Real-time DDoS Detection Engine with Explainable AI
Detects anomalies in Istio traffic using trained ensemble models
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple
from loguru import logger
import yaml
import joblib
from pathlib import Path
from datetime import datetime
import json

# Import feature extractor
from feature_extractor import IstioStatisticalFeatureExtractor

# SHAP for explainability
import shap


class AnomalyExplainer:
    """
    Explain anomaly detection decisions using SHAP
    """
    
    def __init__(self, isolation_forest, feature_names: List[str], background_data: np.ndarray):
        self.isolation_forest = isolation_forest
        self.feature_names = feature_names
        
        # Create SHAP explainer for Isolation Forest
        logger.info("Initializing SHAP explainer...")
        self.if_explainer = shap.TreeExplainer(
            isolation_forest,
            background_data,
            feature_perturbation='tree_path_dependent'
        )
        
        logger.info("SHAP explainer ready")
    
    def explain_anomaly(self, X: np.ndarray, anomaly_score: float) -> Dict:
        """
        Generate SHAP-based explanation for anomaly detection
        
        Returns:
            Dictionary with explanation details
        """
        # Get SHAP values
        shap_values = self.if_explainer.shap_values(X)
        
        # Flatten if needed
        if len(shap_values.shape) > 1:
            shap_values = shap_values[0]
        
        # Create feature importance dict
        feature_importance = {}
        for i, feature_name in enumerate(self.feature_names):
            feature_importance[feature_name] = float(shap_values[i])
        
        # Sort by absolute contribution
        sorted_features = sorted(
            feature_importance.items(),
            key=lambda x: abs(x[1]),
            reverse=True
        )
        
        # Get top contributing features
        top_features = sorted_features[:10]
        
        # Classify severity based on anomaly score
        if anomaly_score < -0.7:
            severity = "CRITICAL"
        elif anomaly_score < -0.5:
            severity = "HIGH"
        elif anomaly_score < -0.3:
            severity = "MEDIUM"
        else:
            severity = "LOW"
        
        explanation = {
            'anomaly_score': float(anomaly_score),
            'severity': severity,
            'top_features': [
                {
                    'name': self._format_feature_name(name),
                    'contribution': float(value),
                    'direction': 'increases' if value > 0 else 'decreases'
                }
                for name, value in top_features
            ],
            'all_contributions': feature_importance,
            'timestamp': datetime.now().isoformat()
        }
        
        return explanation
    
    def generate_human_explanation(self, explanation: Dict, feature_values: Dict) -> str:
        """
        Generate human-readable explanation
        """
        lines = [
            "\n" + "="*70,
            "⚠️  DDoS ATTACK DETECTED - ANOMALY ANALYSIS",
            "="*70,
            f"\n🎯 Anomaly Score: {explanation['anomaly_score']:.3f}",
            f"🚨 Severity: {explanation['severity']}",
            f"🕐 Time: {explanation['timestamp']}",
            "\n" + "-"*70,
            "🔍 TOP CONTRIBUTING FEATURES (SHAP Analysis):",
            "-"*70
        ]
        
        for i, feature in enumerate(explanation['top_features'], 1):
            name = feature['name']
            contribution = feature['contribution']
            direction = feature['direction']
            
            # Get actual feature value if available
            raw_name = self._find_raw_feature_name(name, feature_values)
            value = feature_values.get(raw_name, 'N/A')
            if isinstance(value, float):
                value = f"{value:.2f}"
            
            lines.append(
                f"\n{i:2d}. {name}\n"
                f"    SHAP Value: {abs(contribution):.4f} ({direction} anomaly score)\n"
                f"    Current Value: {value}\n"
                f"    Impact: {self._assess_impact(abs(contribution))}"
            )
        
        lines.extend([
            "\n" + "-"*70,
            "💡 INTERPRETATION:",
            "-"*70,
            self._interpret_anomaly(explanation)
        ])
        
        lines.extend([
            "\n" + "-"*70,
            "🛡️  RECOMMENDED ACTIONS:",
            "-"*70,
            self._recommend_actions(explanation)
        ])
        
        lines.append("\n" + "="*70 + "\n")
        
        return "\n".join(lines)
    
    def _format_feature_name(self, feature_name: str) -> str:
        """Format feature name for display"""
        # Remove window suffix
        name = feature_name
        if '_w10' in name:
            name = name.replace('_w10', ' (10s window)')
        elif '_w30' in name:
            name = name.replace('_w30', ' (30s window)')
        elif '_w60' in name:
            name = name.replace('_w60', ' (60s window)')
        
        # Replace underscores
        name = name.replace('_', ' ').title()
        
        return name
    
    def _find_raw_feature_name(self, formatted_name: str, feature_values: Dict) -> str:
        """Find original feature name from formatted name"""
        for key in feature_values.keys():
            if formatted_name.lower().replace(' ', '_').replace('(', '').replace(')', '') in key.lower():
                return key
        return formatted_name
    
    def _assess_impact(self, contribution: float) -> str:
        """Assess feature impact level"""
        if contribution > 0.1:
            return "Very High Impact"
        elif contribution > 0.05:
            return "High Impact"
        elif contribution > 0.02:
            return "Moderate Impact"
        else:
            return "Low Impact"
    
    def _interpret_anomaly(self, explanation: Dict) -> str:
        """Interpret the anomaly based on top features"""
        top_features = explanation['top_features'][:3]
        
        interpretation_parts = []
        
        # Analyze top features
        for feature in top_features:
            name_lower = feature['name'].lower()
            
            if 'rate' in name_lower and 'request' in name_lower:
                interpretation_parts.append(
                    "• Unusual request rate pattern detected - possible volumetric attack"
                )
            elif 'latency' in name_lower:
                interpretation_parts.append(
                    "• Abnormal latency characteristics - may indicate resource exhaustion"
                )
            elif 'error' in name_lower:
                interpretation_parts.append(
                    "• Elevated error rates - service disruption in progress"
                )
            elif 'size' in name_lower:
                interpretation_parts.append(
                    "• Anomalous packet/request sizes - possible amplification attack"
                )
            elif 'connection' in name_lower:
                interpretation_parts.append(
                    "• Unusual connection patterns - potential SYN flood or connection exhaustion"
                )
        
        if not interpretation_parts:
            interpretation_parts.append(
                "• Multiple metrics deviate from normal baseline - distributed attack pattern"
            )
        
        return "\n".join(interpretation_parts)
    
    def _recommend_actions(self, explanation: Dict) -> str:
        """Recommend mitigation actions based on severity and features"""
        severity = explanation['severity']
        
        actions = []
        
        if severity == "CRITICAL":
            actions.extend([
                "1. IMMEDIATE: Enable rate limiting on edge gateway",
                "2. IMMEDIATE: Activate DDoS mitigation service",
                "3. IMMEDIATE: Scale up backend resources",
                "4. Analyze traffic sources and implement IP filtering",
                "5. Contact ISP/hosting provider for upstream filtering"
            ])
        elif severity == "HIGH":
            actions.extend([
                "1. Enable rate limiting per source IP",
                "2. Activate circuit breakers on overloaded services",
                "3. Scale horizontal pods for affected services",
                "4. Monitor for escalation",
                "5. Prepare DDoS mitigation activation"
            ])
        elif severity == "MEDIUM":
            actions.extend([
                "1. Monitor traffic patterns closely",
                "2. Enable request throttling if needed",
                "3. Check service health metrics",
                "4. Review recent configuration changes",
                "5. Alert on-call team if pattern persists"
            ])
        else:  # LOW
            actions.extend([
                "1. Log anomaly for pattern analysis",
                "2. Continue monitoring",
                "3. No immediate action required",
                "4. Review if pattern repeats"
            ])
        
        return "\n".join(actions)


class DDoSDetectionEngine:
    """
    Real-time DDoS detection engine using ensemble anomaly detection
    """
    
    def __init__(self, model_path: str, config_path: str = "config.yaml",
                 prometheus_url: str = "http://localhost:9090"):
        
        # Load configuration
        with open(config_path, 'r') as f:
            self.config = yaml.safe_load(f)
        
        # Load trained models
        logger.info(f"Loading models from {model_path}")
        ensemble = joblib.load(model_path)
        
        self.isolation_forest = ensemble['isolation_forest']
        self.one_class_svm = ensemble['one_class_svm']
        self.scaler = ensemble['scaler']
        self.feature_names = ensemble['feature_names']
        
        # Model weights
        self.if_weight = self.config['model']['ensemble']['isolation_forest']
        self.svm_weight = self.config['model']['ensemble']['one_class_svm']
        
        # Detection threshold
        self.threshold = self.config['detection']['anomaly_threshold']
        
        # Initialize feature extractor
        self.feature_extractor = IstioStatisticalFeatureExtractor(
            prometheus_url=prometheus_url,
            config_path=config_path
        )
        
        # Initialize explainer (need background data)
        # Use a small synthetic background for now
        background = np.random.randn(100, len(self.feature_names))
        self.explainer = AnomalyExplainer(
            self.isolation_forest,
            self.feature_names,
            background
        )
        
        # Alert tracking
        self.last_alert_time = None
        self.consecutive_detections = 0
        
        logger.info("Detection engine initialized")
    
    def extract_features(self) -> Tuple[np.ndarray, Dict]:
        """Extract features from Istio metrics"""
        features_dict = self.feature_extractor.extract_all_features()
        
        # Convert to ordered array matching training features
        feature_vector = []
        for name in self.feature_names:
            feature_vector.append(features_dict.get(name, 0.0))
        
        X = np.array(feature_vector).reshape(1, -1)
        
        # Scale features
        X_scaled = self.scaler.transform(X)
        
        return X_scaled, features_dict
    
    def detect_anomaly(self) -> Tuple[bool, float, Dict]:
        """
        Detect if current traffic is anomalous
        
        Returns:
            (is_anomaly, anomaly_score, explanation)
        """
        # Extract features
        X, features_dict = self.extract_features()
        
        # Get predictions from both models
        if_score = self.isolation_forest.decision_function(X)[0]
        svm_score = self.one_class_svm.decision_function(X)[0]
        
        # Ensemble score
        anomaly_score = (if_score * self.if_weight + svm_score * self.svm_weight)
        
        # Check if anomaly
        is_anomaly = anomaly_score < self.threshold
        
        # Generate explanation if anomaly
        explanation = None
        if is_anomaly:
            explanation = self.explainer.explain_anomaly(X, anomaly_score)
            explanation['feature_values'] = features_dict
            explanation['model_scores'] = {
                'isolation_forest': float(if_score),
                'one_class_svm': float(svm_score),
                'ensemble': float(anomaly_score)
            }
        
        return is_anomaly, anomaly_score, explanation
    
    def should_alert(self) -> bool:
        """Determine if alert should be sent based on cooldown and consecutive detections"""
        min_consecutive = self.config['detection']['min_consecutive_detections']
        cooldown = self.config['detection']['alert_cooldown']
        
        # Check consecutive detections
        if self.consecutive_detections < min_consecutive:
            return False
        
        # Check cooldown
        if self.last_alert_time is not None:
            time_since_alert = (datetime.now() - self.last_alert_time).total_seconds()
            if time_since_alert < cooldown:
                return False
        
        return True
    
    def process_detection(self) -> Optional[Dict]:
        """
        Main detection loop - check current traffic and return alert if needed
        
        Returns:
            Alert dict if attack detected, None otherwise
        """
        is_anomaly, score, explanation = self.detect_anomaly()
        
        if is_anomaly:
            self.consecutive_detections += 1
            logger.warning(f"Anomaly detected! Score: {score:.3f} "
                          f"(consecutive: {self.consecutive_detections})")
            
            if self.should_alert():
                # Generate human explanation
                human_explanation = self.explainer.generate_human_explanation(
                    explanation,
                    explanation['feature_values']
                )
                
                alert = {
                    'type': 'ddos_attack',
                    'timestamp': datetime.now().isoformat(),
                    'anomaly_score': score,
                    'severity': explanation['severity'],
                    'explanation': explanation,
                    'human_explanation': human_explanation
                }
                
                # Update alert time
                self.last_alert_time = datetime.now()
                self.consecutive_detections = 0
                
                return alert
        else:
            # Reset consecutive counter
            self.consecutive_detections = 0
            logger.debug(f"Normal traffic. Score: {score:.3f}")
        
        return None
    
    def save_alert(self, alert: Dict):
        """Save alert to log file"""
        log_path = Path(self.config['monitoring']['anomaly_log_path'])
        log_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(log_path, 'a') as f:
            f.write(json.dumps(alert) + '\n')
        
        logger.info(f"Alert saved to {log_path}")


if __name__ == "__main__":
    # Test detection
    logger.info("Testing detection engine...")
    
    engine = DDoSDetectionEngine(
        model_path="models/ensemble_detector.pkl",
        prometheus_url="http://localhost:9090"
    )
    
    alert = engine.process_detection()
    
    if alert:
        print(alert['human_explanation'])
    else:
        print("No anomaly detected")
