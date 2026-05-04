#!/usr/bin/env python3
"""
CLI Monitoring Interface for DDoS Detection
Real-time monitoring with alerts and explanations
"""

import argparse
import time
import signal
import sys
from datetime import datetime
from loguru import logger
from pathlib import Path

# Import detector engine from src subdirectory
try:
    from src.detector_engine import DDoSDetectionEngine
except ImportError:
    # Fallback for different execution contexts
    sys.path.insert(0, str(Path(__file__).parent / 'src'))
    from detector_engine import DDoSDetectionEngine


class CLIMonitor:
    """
    Command-line interface for real-time DDoS detection monitoring
    """
    
    def __init__(self, model_path: str, prometheus_url: str, config_path: str,
                 interval: int = 15):
        self.engine = DDoSDetectionEngine(
            model_path=model_path,
            config_path=config_path,
            prometheus_url=prometheus_url
        )
        self.interval = interval
        self.running = False
        self.stats = {
            'checks': 0,
            'anomalies': 0,
            'alerts': 0,
            'start_time': None
        }
        
        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        logger.info("\nShutdown signal received. Stopping monitor...")
        self.running = False
    
    def print_banner(self):
        """Print startup banner"""
        banner = """
╔═══════════════════════════════════════════════════════════════════╗
║                                                                   ║
║      ML-OPTIMIZED DDoS DETECTOR - REAL-TIME MONITORING           ║
║                                                                   ║
║  Anomaly Detection: Isolation Forest + One-Class SVM             ║
║  Training Data: CICDDoS2019 (Normal Traffic Only)                ║
║  Explainability: SHAP-based Feature Analysis                     ║
║                                                                   ║
╚═══════════════════════════════════════════════════════════════════╝
        """
        print(banner)
        print(f"📊 Monitoring Interval: {self.interval} seconds")
        print(f"🎯 Anomaly Threshold: {self.engine.threshold}")
        print(f"🔗 Prometheus: {self.engine.feature_extractor.prometheus_url}")
        print(f"⏰ Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("\n" + "="*70)
        print("STATUS: Monitoring started. Press Ctrl+C to stop.")
        print("="*70 + "\n")
    
    def print_status(self, is_anomaly: bool, score: float):
        """Print current status"""
        timestamp = datetime.now().strftime('%H:%M:%S')
        
        if is_anomaly:
            status = "🚨 ANOMALY"
            color = "\033[91m"  # Red
        else:
            status = "✅ NORMAL "
            color = "\033[92m"  # Green
        
        reset = "\033[0m"
        
        print(f"{timestamp} | {color}{status}{reset} | Score: {score:+.3f} | "
              f"Checks: {self.stats['checks']} | Anomalies: {self.stats['anomalies']} | "
              f"Alerts: {self.stats['alerts']}")
    
    def print_summary(self):
        """Print monitoring summary"""
        if self.stats['start_time']:
            duration = (datetime.now() - self.stats['start_time']).total_seconds()
            duration_str = f"{int(duration // 3600)}h {int((duration % 3600) // 60)}m {int(duration % 60)}s"
        else:
            duration_str = "N/A"
        
        print("\n" + "="*70)
        print("MONITORING SUMMARY")
        print("="*70)
        print(f"Duration: {duration_str}")
        print(f"Total Checks: {self.stats['checks']}")
        print(f"Anomalies Detected: {self.stats['anomalies']}")
        print(f"Alerts Sent: {self.stats['alerts']}")
        if self.stats['checks'] > 0:
            anomaly_rate = (self.stats['anomalies'] / self.stats['checks']) * 100
            print(f"Anomaly Rate: {anomaly_rate:.1f}%")
        print("="*70 + "\n")
    
    def run(self):
        """Main monitoring loop"""
        self.print_banner()
        self.running = True
        self.stats['start_time'] = datetime.now()
        
        try:
            while self.running:
                # Process detection
                self.stats['checks'] += 1
                
                try:
                    alert = self.engine.process_detection()
                    
                    # Get current anomaly status for display
                    is_anomaly = alert is not None or self.engine.consecutive_detections > 0
                    
                    # Estimate score (use last detection)
                    if alert:
                        score = alert['anomaly_score']
                        self.stats['anomalies'] += 1
                    else:
                        # Run a quick check to get score
                        _, score, _ = self.engine.detect_anomaly()
                        if score < self.engine.threshold:
                            self.stats['anomalies'] += 1
                    
                    # Print status
                    self.print_status(is_anomaly, score)
                    
                    # Handle alert
                    if alert:
                        self.stats['alerts'] += 1
                        print(alert['human_explanation'])
                        
                        # Save alert
                        self.engine.save_alert(alert)
                        
                        # Beep (if terminal supports it)
                        print("\a")
                
                except Exception as e:
                    logger.error(f"Error in detection loop: {e}")
                    print(f"⚠️  Error: {e}")
                
                # Sleep until next check
                if self.running:
                    time.sleep(self.interval)
        
        except KeyboardInterrupt:
            logger.info("Interrupted by user")
        
        finally:
            self.print_summary()
            logger.info("Monitor stopped")
    
    def run_once(self):
        """Run single detection check"""
        self.stats['checks'] = 1
        self.stats['start_time'] = datetime.now()
        
        print("\n🔍 Running single detection check...\n")
        
        try:
            alert = self.engine.process_detection()
            
            if alert:
                print(alert['human_explanation'])
                print(f"\n✅ Alert saved to {self.engine.config['monitoring']['anomaly_log_path']}")
                return 1  # Attack detected
            else:
                print("="*70)
                print("✅ NO ANOMALY DETECTED - Traffic appears normal")
                print("="*70)
                
                # Show current metrics
                _, score, _ = self.engine.detect_anomaly()
                print(f"\n📊 Anomaly Score: {score:+.3f} (threshold: {self.engine.threshold})")
                print(f"   Status: {'ANOMALOUS' if score < self.engine.threshold else 'NORMAL'}")
                
                return 0  # Normal traffic
        
        except Exception as e:
            logger.error(f"Detection error: {e}")
            print(f"\n❌ Error: {e}")
            return 2  # Error


def main():
    parser = argparse.ArgumentParser(
        description='Real-time DDoS detection monitoring CLI',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Continuous monitoring (default 15s interval)
  python cli_monitor.py --model models/ensemble_detector.pkl
  
  # Monitoring with custom interval
  python cli_monitor.py --model models/ensemble_detector.pkl --interval 30
  
  # Single check (no continuous monitoring)
  python cli_monitor.py --model models/ensemble_detector.pkl --once
  
  # Custom Prometheus URL
  python cli_monitor.py --model models/ensemble_detector.pkl \\
      --prometheus http://192.168.1.100:9090
        """
    )
    
    parser.add_argument(
        '--model',
        required=True,
        help='Path to trained model file (ensemble_detector.pkl)'
    )
    parser.add_argument(
        '--prometheus',
        default='http://localhost:9090',
        help='Prometheus URL (default: http://localhost:9090)'
    )
    parser.add_argument(
        '--config',
        default='config.yaml',
        help='Configuration file path (default: config.yaml)'
    )
    parser.add_argument(
        '--interval',
        type=int,
        default=15,
        help='Monitoring interval in seconds (default: 15)'
    )
    parser.add_argument(
        '--once',
        action='store_true',
        help='Run single detection check and exit'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )
    
    args = parser.parse_args()
    
    # Setup logging
    if args.verbose:
        logger.remove()
        logger.add(sys.stderr, level="DEBUG")
    else:
        logger.remove()
        logger.add(sys.stderr, level="INFO")
    
    # Verify model exists
    if not Path(args.model).exists():
        print(f"❌ Error: Model file not found: {args.model}")
        print("\nPlease train the model first:")
        print("  python train_detector.py --dataset /path/to/cicddos2019 --output models/")
        sys.exit(1)
    
    # Verify config exists
    if not Path(args.config).exists():
        print(f"❌ Error: Config file not found: {args.config}")
        sys.exit(1)
    
    # Create monitor
    monitor = CLIMonitor(
        model_path=args.model,
        prometheus_url=args.prometheus,
        config_path=args.config,
        interval=args.interval
    )
    
    # Run
    if args.once:
        exit_code = monitor.run_once()
        sys.exit(exit_code)
    else:
        monitor.run()


if __name__ == "__main__":
    main()
