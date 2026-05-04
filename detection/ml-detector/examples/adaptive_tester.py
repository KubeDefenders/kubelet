#!/usr/bin/env python3
"""
Adaptive Model Tester and Refiner
Continuously tests detection accuracy and refines model threshold
"""

import time
import subprocess
import json
from pathlib import Path
from loguru import logger
import numpy as np


class AdaptiveDetectorTester:
    """Test and refine the detector based on real performance"""
    
    def __init__(self):
        self.results = {
            'normal_checks': [],
            'attack_checks': [],
            'false_positives': 0,
            'false_negatives': 0,
            'true_positives': 0,
            'true_negatives': 0
        }
        self.log_file = Path('logs/adaptive_testing.jsonl')
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
    
    def detect(self) -> tuple:
        """Run detection and return result"""
        try:
            result = subprocess.run(
                ['python3', 'practical_detector.py', 'detect'],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            output = result.stdout + result.stderr
            is_attack = 'ATTACK DETECTED' in output
            
            # Extract score
            score = 0.0
            for line in output.split('\n'):
                if 'score:' in line.lower():
                    try:
                        score = float(line.split('score:')[1].split()[0])
                    except:
                        pass
            
            return is_attack, score
        except Exception as e:
            logger.error(f"Detection error: {e}")
            return False, 0.0
    
    def test_normal_traffic(self, num_checks: int = 5, interval: int = 3) -> dict:
        """Test detection on normal traffic"""
        logger.info(f"Testing normal traffic detection ({num_checks} checks)")
        
        results = []
        for i in range(num_checks):
            is_attack, score = self.detect()
            results.append({'attack': is_attack, 'score': score})
            
            if is_attack:
                self.results['false_positives'] += 1
                logger.warning(f"❌ False positive! Score: {score:.3f}")
            else:
                self.results['true_negatives'] += 1
                logger.info(f"✅ Correct negative. Score: {score:.3f}")
            
            time.sleep(interval)
        
        self.results['normal_checks'].extend(results)
        
        fp_rate = self.results['false_positives'] / max(1, 
                    self.results['false_positives'] + self.results['true_negatives'])
        
        return {
            'checks': num_checks,
            'false_positives': self.results['false_positives'],
            'false_positive_rate': fp_rate,
            'scores': [r['score'] for r in results]
        }
    
    def test_attack_traffic(self, num_checks: int = 5, interval: int = 3) -> dict:
        """Test detection during attack"""
        logger.info(f"Testing attack detection ({num_checks} checks)")
        
        results = []
        for i in range(num_checks):
            is_attack, score = self.detect()
            results.append({'attack': is_attack, 'score': score})
            
            if is_attack:
                self.results['true_positives'] += 1
                logger.info(f"✅ Detected! Score: {score:.3f}")
            else:
                self.results['false_negatives'] += 1
                logger.warning(f"❌ Missed attack! Score: {score:.3f}")
            
            time.sleep(interval)
        
        self.results['attack_checks'].extend(results)
        
        detection_rate = self.results['true_positives'] / max(1,
                        self.results['true_positives'] + self.results['false_negatives'])
        
        return {
            'checks': num_checks,
            'true_positives': self.results['true_positives'],
            'detection_rate': detection_rate,
            'scores': [r['score'] for r in results]
        }
    
    def compute_metrics(self) -> dict:
        """Compute overall performance metrics"""
        tp = self.results['true_positives']
        tn = self.results['true_negatives']
        fp = self.results['false_positives']
        fn = self.results['false_negatives']
        
        total = tp + tn + fp + fn
        if total == 0:
            return {}
        
        accuracy = (tp + tn) / total
        precision = tp / max(1, tp + fp)
        recall = tp / max(1, tp + fn)
        f1 = 2 * (precision * recall) / max(0.001, precision + recall)
        fpr = fp / max(1, fp + tn)
        
        return {
            'accuracy': accuracy,
            'precision': precision,
            'recall': recall,
            'f1_score': f1,
            'false_positive_rate': fpr,
            'true_positives': tp,
            'true_negatives': tn,
            'false_positives': fp,
            'false_negatives': fn,
            'total_checks': total
        }
    
    def recommend_refinement(self) -> dict:
        """Recommend model refinements based on results"""
        metrics = self.compute_metrics()
        
        recommendations = []
        
        if metrics.get('false_positive_rate', 0) > 0.2:
            recommendations.append({
                'issue': 'High false positive rate',
                'action': 'Decrease contamination parameter (make less sensitive)',
                'suggested_value': 'contamination=0.10'
            })
        
        if metrics.get('recall', 0) < 0.7:
            recommendations.append({
                'issue': 'Low detection rate',
                'action': 'Increase contamination parameter (make more sensitive)',
                'suggested_value': 'contamination=0.20'
            })
        
        if 0.7 <= metrics.get('recall', 0) <= 0.9 and metrics.get('false_positive_rate', 0) < 0.15:
            recommendations.append({
                'issue': 'Good balance',
                'action': 'Current settings are working well',
                'suggested_value': 'No changes needed'
            })
        
        return {
            'metrics': metrics,
            'recommendations': recommendations
        }
    
    def save_results(self):
        """Save results to log file"""
        with open(self.log_file, 'a') as f:
            json.dump({
                'timestamp': time.time(),
                'results': self.results,
                'metrics': self.compute_metrics()
            }, f)
            f.write('\n')


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Test and refine detector')
    parser.add_argument('--mode', choices=['normal', 'attack', 'full'], default='full',
                       help='Test mode')
    parser.add_argument('--checks', type=int, default=5, help='Number of checks per phase')
    parser.add_argument('--interval', type=int, default=3, help='Seconds between checks')
    args = parser.parse_args()
    
    tester = AdaptiveDetectorTester()
    
    if args.mode in ['normal', 'full']:
        logger.info("="*70)
        logger.info("TESTING NORMAL TRAFFIC DETECTION")
        logger.info("="*70)
        normal_results = tester.test_normal_traffic(args.checks, args.interval)
        logger.info(f"Normal traffic results: {normal_results}")
    
    if args.mode in ['attack', 'full']:
        logger.info("\n" + "="*70)
        logger.info("TESTING ATTACK DETECTION")
        logger.info("="*70)
        logger.info("⚠️  Make sure an attack is running!")
        input("Press Enter when attack is active...")
        
        attack_results = tester.test_attack_traffic(args.checks, args.interval)
        logger.info(f"Attack detection results: {attack_results}")
    
    # Compute and display metrics
    logger.info("\n" + "="*70)
    logger.info("OVERALL PERFORMANCE METRICS")
    logger.info("="*70)
    
    refinement = tester.recommend_refinement()
    metrics = refinement['metrics']
    
    logger.info(f"Accuracy: {metrics.get('accuracy', 0):.2%}")
    logger.info(f"Precision: {metrics.get('precision', 0):.2%}")
    logger.info(f"Recall (Detection Rate): {metrics.get('recall', 0):.2%}")
    logger.info(f"F1 Score: {metrics.get('f1_score', 0):.2%}")
    logger.info(f"False Positive Rate: {metrics.get('false_positive_rate', 0):.2%}")
    
    logger.info("\n" + "="*70)
    logger.info("RECOMMENDATIONS")
    logger.info("="*70)
    for rec in refinement['recommendations']:
        logger.info(f"Issue: {rec['issue']}")
        logger.info(f"Action: {rec['action']}")
        logger.info(f"Suggested: {rec['suggested_value']}")
        logger.info("-"*70)
    
    tester.save_results()
    logger.info(f"\nResults saved to {tester.log_file}")


if __name__ == "__main__":
    main()
