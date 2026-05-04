#!/usr/bin/env python3
import time
import sys
from datetime import datetime
from loguru import logger
import argparse
from practical_detector import PracticalDetector

# Configure logger for clean monitoring output
logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{message}</level>",
    level="INFO"
)


class ContinuousMonitor:
    """Continuous anomaly detection monitor"""
    
    def __init__(self, model_path: str, check_interval: int = 5, consecutive_threshold: int = 2):
        self.model_path = model_path
        self.check_interval = check_interval
        self.consecutive_threshold = consecutive_threshold
        self.detector = None
        self.consecutive_anomalies = 0
        self.consecutive_normal = 0
        self.alert_state = False
        self.stats = {
            'total_checks': 0,
            'anomalies': 0,
            'normal': 0,
            'alerts_triggered': 0
        }
        
    def load_model(self):
        """Load detector model"""
        logger.info(f"Loading model from {self.model_path}")
        self.detector = PracticalDetector()
        self.detector.load(self.model_path)
        logger.success("Model loaded successfully")
    
    def check(self):
        """Perform single detection check"""
        try:
            is_anomaly, score, explanation = self.detector.detect(explain=True)
            self.stats['total_checks'] += 1
            
            if is_anomaly:
                self.stats['anomalies'] += 1
                self.consecutive_anomalies += 1
                self.consecutive_normal = 0
                
                # Check if we should trigger alert
                if self.consecutive_anomalies >= self.consecutive_threshold and not self.alert_state:
                    self.alert_state = True
                    self.stats['alerts_triggered'] += 1
                    logger.critical(f"🚨 ALERT: ATTACK DETECTED (score: {score:.3f}, {self.consecutive_anomalies} consecutive)")
                    
                    if explanation:
                        logger.warning("Top attack indicators:")
                        for feature, contribution in explanation['top_features'][:3]:
                            direction = "↑" if contribution > 0 else "↓"
                            logger.warning(f"  {direction} {feature}: {contribution:.4f}")
                else:
                    logger.warning(f"⚠️  Anomaly detected (score: {score:.3f}, count: {self.consecutive_anomalies}/{self.consecutive_threshold})")
                    
            else:
                self.stats['normal'] += 1
                self.consecutive_normal += 1
                self.consecutive_anomalies = 0
                
                # Clear alert state after consecutive normal checks
                if self.alert_state and self.consecutive_normal >= self.consecutive_threshold:
                    logger.success(f"✅ ALERT CLEARED: Traffic returned to normal")
                    self.alert_state = False
                
                logger.info(f"✓ Normal traffic (score: {score:.3f})")
                
        except Exception as e:
            logger.error(f"Check failed: {e}")
    
    def run(self):
        """Run continuous monitoring"""
        logger.info("="*70)
        logger.info("🔍 ML DDoS CONTINUOUS MONITOR")
        logger.info(f"Check interval: {self.check_interval}s")
        logger.info(f"Alert threshold: {self.consecutive_threshold} consecutive anomalies")
        logger.info("Press Ctrl+C to stop")
        logger.info("="*70)
        
        self.load_model()
        
        try:
            while True:
                self.check()
                time.sleep(self.check_interval)
                
        except KeyboardInterrupt:
            logger.info("\n" + "="*70)
            logger.info("📊 MONITORING SUMMARY")
            logger.info(f"Total checks: {self.stats['total_checks']}")
            logger.info(f"Normal: {self.stats['normal']} ({self.stats['normal']/max(self.stats['total_checks'],1)*100:.1f}%)")
            logger.info(f"Anomalies: {self.stats['anomalies']} ({self.stats['anomalies']/max(self.stats['total_checks'],1)*100:.1f}%)")
            logger.info(f"Alerts triggered: {self.stats['alerts_triggered']}")
            logger.info("="*70)
            logger.success("Monitor stopped")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Continuous DDoS monitoring")
    parser.add_argument("--model", default="models/practical_detector.pkl", help="Path to model file")
    parser.add_argument("--interval", type=int, default=5, help="Check interval in seconds")
    parser.add_argument("--threshold", type=int, default=2, help="Consecutive detections before alert")
    
    args = parser.parse_args()
    
    monitor = ContinuousMonitor(
        model_path=args.model,
        check_interval=args.interval,
        consecutive_threshold=args.threshold
    )
    
    monitor.run()
