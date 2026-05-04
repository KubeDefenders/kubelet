#!/usr/bin/env python3
"""
Train Anomaly Detection Models on CICDDoS2019 Dataset
Uses only NORMAL traffic to learn baseline behavior patterns
"""

import argparse
import json
import numpy as np
import pandas as pd
from pathlib import Path
from typing import Dict, Tuple
from loguru import logger
import yaml
import joblib

# ML libraries
from sklearn.ensemble import IsolationForest
from sklearn.svm import OneClassSVM
from sklearn.preprocessing import RobustScaler, StandardScaler
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, confusion_matrix


class CICDDoS2019Trainer:
    """
    Train anomaly detection models on CICDDoS2019 normal traffic
    """
    
    def __init__(self, config_path: str = "config.yaml"):
        with open(config_path, 'r') as f:
            self.config = yaml.safe_load(f)
        
        self.scaler = None
        self.isolation_forest = None
        self.one_class_svm = None
        
        logger.info("Initialized CICDDoS2019 trainer")
    
    def load_cicddos_dataset(self, dataset_path: str) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Load CICDDoS2019 dataset and separate normal/attack samples
        """
        logger.info(f"Loading CICDDoS2019 from {dataset_path}")
        
        dataset_path = Path(dataset_path)
        all_data = []
        
        # Try Parquet files first
        parquet_files = list(dataset_path.glob("*.parquet"))
        if parquet_files:
            logger.info(f"Found {len(parquet_files)} Parquet files")
            for parquet_file in parquet_files:
                try:
                    df = pd.read_parquet(parquet_file)
                    all_data.append(df)
                    logger.info(f"Loaded {len(df)} rows from {parquet_file.name}")
                except Exception as e:
                    logger.error(f"Error reading {parquet_file}: {e}")
        else:
            # Try CSV files
            csv_files = list(dataset_path.glob("*.csv"))
            if csv_files:
                logger.info(f"Found {len(csv_files)} CSV files")
                for csv_file in csv_files:
                    try:
                        df = pd.read_csv(csv_file, low_memory=False)
                        all_data.append(df)
                        logger.info(f"Loaded {len(df)} rows from {csv_file.name}")
                    except Exception as e:
                        logger.error(f"Error reading {csv_file}: {e}")
        
        if not all_data:
            raise ValueError("No data loaded")
        
        # Combine dataframes
        combined_df = pd.concat(all_data, ignore_index=True)
        logger.info(f"Total rows: {len(combined_df)}")
        
        # Clean column names
        combined_df.columns = combined_df.columns.str.strip()
        
        # Identify label column (handle both "Label" and " Label")
        if 'Label' in combined_df.columns:
            label_col = 'Label'
        elif ' Label' in combined_df.columns:
            label_col = ' Label'
        else:
            raise ValueError("No Label column found in dataset")
        
        # Handle infinite/NaN values
        combined_df = combined_df.replace([np.inf, -np.inf], np.nan)
        combined_df = combined_df.fillna(0)
        
        # Separate normal and attack samples
        # Check for case variations (Benign, BENIGN, benign)
        normal_df = combined_df[combined_df[label_col].str.upper() == 'BENIGN'].copy()
        attack_df = combined_df[combined_df[label_col].str.upper() != 'BENIGN'].copy()
        
        logger.info(f"Normal samples: {len(normal_df)}")
        logger.info(f"Attack samples: {len(attack_df)}")
        
        # Drop label column from features
        if label_col in normal_df.columns:
            normal_df = normal_df.drop(columns=[label_col])
        if label_col in attack_df.columns:
            attack_df = attack_df.drop(columns=[label_col])
        
        # Drop non-numeric columns
        normal_df = normal_df.select_dtypes(include=[np.number])
        attack_df = attack_df.select_dtypes(include=[np.number])
        
        return normal_df, attack_df
    
    def select_statistical_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Select features that represent statistical flow characteristics
        These transfer better to Istio metrics than absolute values
        """
        logger.info("Selecting statistical features...")
        
        # Drop non-statistical columns that don't transfer well
        drop_patterns = [
            'protocol',
            'ip',
            'port',
            'timestamp',
            'flow_id',
            'src',
            'dst',
            'source',
            'destination'
        ]
        
        selected_cols = []
        for col in df.columns:
            col_lower = col.lower()
            # Skip columns that match drop patterns
            if any(pattern in col_lower for pattern in drop_patterns):
                continue
            selected_cols.append(col)
        
        logger.info(f"Selected {len(selected_cols)} statistical features")
        return df[selected_cols]
    
    def remove_correlated_features(self, df: pd.DataFrame, threshold: float = 0.95) -> pd.DataFrame:
        """
        Remove highly correlated features to reduce redundancy
        """
        logger.info(f"Removing features with correlation > {threshold}")
        
        # Compute correlation matrix
        corr_matrix = df.corr().abs()
        
        # Select upper triangle
        upper = corr_matrix.where(
            np.triu(np.ones(corr_matrix.shape), k=1).astype(bool)
        )
        
        # Find features with correlation greater than threshold
        to_drop = [column for column in upper.columns if any(upper[column] > threshold)]
        
        logger.info(f"Dropping {len(to_drop)} highly correlated features")
        
        return df.drop(columns=to_drop)
    
    def preprocess_features(self, df: pd.DataFrame, fit: bool = True) -> np.ndarray:
        """
        Preprocess features: scaling and outlier handling
        """
        # Remove low-variance features
        if fit:
            variances = df.var()
            low_var_cols = variances[variances < 0.01].index
            if len(low_var_cols) > 0:
                logger.info(f"Removing {len(low_var_cols)} low-variance features")
                df = df.drop(columns=low_var_cols)
                self.selected_features = df.columns.tolist()
        else:
            # Use stored feature names
            df = df[self.selected_features]
        
        # Convert to numpy
        X = df.values
        
        # Initialize scaler
        if fit:
            scaler_type = self.config['model']['preprocessing']['scaler']
            if scaler_type == 'robust':
                self.scaler = RobustScaler()
            else:
                self.scaler = StandardScaler()
            
            X_scaled = self.scaler.fit_transform(X)
        else:
            X_scaled = self.scaler.transform(X)
        
        # Clip outliers
        if self.config['model']['preprocessing']['clip_outliers']:
            percentile = self.config['model']['preprocessing']['outlier_percentile']
            clip_value = np.percentile(np.abs(X_scaled), percentile)
            X_scaled = np.clip(X_scaled, -clip_value, clip_value)
        
        return X_scaled
    
    def train_isolation_forest(self, X_train: np.ndarray) -> IsolationForest:
        """
        Train Isolation Forest on normal traffic
        """
        logger.info("Training Isolation Forest...")
        
        config = self.config['model']['isolation_forest']
        
        model = IsolationForest(
            n_estimators=config['n_estimators'],
            max_samples=config['max_samples'],
            contamination=config['contamination'],
            random_state=config['random_state'],
            n_jobs=config['n_jobs'],
            verbose=1
        )
        
        model.fit(X_train)
        
        logger.info("Isolation Forest training complete")
        return model
    
    def train_one_class_svm(self, X_train: np.ndarray) -> OneClassSVM:
        """
        Train One-Class SVM on normal traffic
        """
        logger.info("Training One-Class SVM...")
        
        config = self.config['model']['one_class_svm']
        
        # Subsample for SVM (it's slower)
        if len(X_train) > 10000:
            logger.info("Subsampling to 10000 samples for SVM training")
            indices = np.random.choice(len(X_train), 10000, replace=False)
            X_train_sub = X_train[indices]
        else:
            X_train_sub = X_train
        
        model = OneClassSVM(
            kernel=config['kernel'],
            gamma=config['gamma'],
            nu=config['nu'],
            verbose=True
        )
        
        model.fit(X_train_sub)
        
        logger.info("One-Class SVM training complete")
        return model
    
    def evaluate_models(self, X_val: np.ndarray, y_val: np.ndarray) -> Dict:
        """
        Evaluate models on validation set (mix of normal and attack)
        """
        logger.info("Evaluating models...")
        
        # Isolation Forest predictions
        if_pred = self.isolation_forest.predict(X_val)
        if_pred_binary = (if_pred == -1).astype(int)  # -1 = anomaly
        
        # One-Class SVM predictions
        svm_pred = self.one_class_svm.predict(X_val)
        svm_pred_binary = (svm_pred == -1).astype(int)
        
        # Ensemble prediction
        if_weight = self.config['model']['ensemble']['isolation_forest']
        svm_weight = self.config['model']['ensemble']['one_class_svm']
        
        ensemble_score = (if_pred * if_weight + svm_pred * svm_weight)
        ensemble_pred = (ensemble_score < 0).astype(int)
        
        # Compute metrics
        results = {
            'isolation_forest': {
                'report': classification_report(y_val, if_pred_binary, output_dict=True),
                'confusion_matrix': confusion_matrix(y_val, if_pred_binary).tolist()
            },
            'one_class_svm': {
                'report': classification_report(y_val, svm_pred_binary, output_dict=True),
                'confusion_matrix': confusion_matrix(y_val, svm_pred_binary).tolist()
            },
            'ensemble': {
                'report': classification_report(y_val, ensemble_pred, output_dict=True),
                'confusion_matrix': confusion_matrix(y_val, ensemble_pred).tolist()
            }
        }
        
        # Log results
        logger.info("\n=== Isolation Forest ===")
        logger.info(f"Accuracy: {results['isolation_forest']['report']['accuracy']:.3f}")
        if '1' in results['isolation_forest']['report']:
            logger.info(f"Recall (Attack): {results['isolation_forest']['report']['1']['recall']:.3f}")
        
        logger.info("\n=== One-Class SVM ===")
        logger.info(f"Accuracy: {results['one_class_svm']['report']['accuracy']:.3f}")
        if '1' in results['one_class_svm']['report']:
            logger.info(f"Recall (Attack): {results['one_class_svm']['report']['1']['recall']:.3f}")
        
        logger.info("\n=== Ensemble ===")
        logger.info(f"Accuracy: {results['ensemble']['report']['accuracy']:.3f}")
        if '1' in results['ensemble']['report']:
            logger.info(f"Recall (Attack): {results['ensemble']['report']['1']['recall']:.3f}")
        
        return results
    
    def save_models(self, output_dir: str = "models"):
        """
        Save trained models and preprocessing components
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Save models
        ensemble = {
            'isolation_forest': self.isolation_forest,
            'one_class_svm': self.one_class_svm,
            'scaler': self.scaler,
            'feature_names': self.selected_features,
            'config': self.config
        }
        
        model_path = output_dir / 'ensemble_detector.pkl'
        joblib.dump(ensemble, model_path)
        logger.info(f"Saved ensemble model to {model_path}")
        
        # Save feature names separately
        feature_path = output_dir / 'feature_names.json'
        with open(feature_path, 'w') as f:
            json.dump(self.selected_features, f, indent=2)
        logger.info(f"Saved feature names to {feature_path}")
    
    def train(self, dataset_path: str, output_dir: str = "models"):
        """
        Complete training pipeline
        """
        # Load dataset
        normal_df, attack_df = self.load_cicddos_dataset(dataset_path)
        
        # Select statistical features
        normal_df = self.select_statistical_features(normal_df)
        attack_df = self.select_statistical_features(attack_df)
        
        # Remove correlated features
        normal_df = self.remove_correlated_features(normal_df)
        
        # Sample if needed
        sample_size = self.config['training']['dataset']['sample_size']
        if len(normal_df) > sample_size:
            logger.info(f"Sampling {sample_size} normal samples")
            normal_df = normal_df.sample(n=sample_size, random_state=42)
        
        # Preprocess
        X_normal = self.preprocess_features(normal_df, fit=True)
        
        # Split normal data for validation
        val_split = self.config['training']['dataset']['validation_split']
        X_train, X_val_normal = train_test_split(
            X_normal, test_size=val_split, random_state=42
        )
        
        # Train models
        self.isolation_forest = self.train_isolation_forest(X_train)
        self.one_class_svm = self.train_one_class_svm(X_train)
        
        # Prepare validation set (mix of normal and attack)
        if len(attack_df) > 1000:
            attack_df = attack_df.sample(n=1000, random_state=42)
        
        X_val_attack = self.preprocess_features(attack_df, fit=False)
        
        X_val = np.vstack([X_val_normal, X_val_attack])
        y_val = np.hstack([
            np.zeros(len(X_val_normal)),
            np.ones(len(X_val_attack))
        ])
        
        # Evaluate
        results = self.evaluate_models(X_val, y_val)
        
        # Save models
        self.save_models(output_dir)
        
        # Save training stats
        stats_path = Path(output_dir) / 'training_stats.json'
        with open(stats_path, 'w') as f:
            json.dump(results, f, indent=2)
        
        logger.info("Training complete!")


def main():
    parser = argparse.ArgumentParser(description='Train anomaly detection models on CICDDoS2019')
    parser.add_argument('--dataset', required=True, help='Path to CICDDoS2019 dataset directory')
    parser.add_argument('--config', default='config.yaml', help='Path to config file')
    parser.add_argument('--output', default='models', help='Output directory for models')
    args = parser.parse_args()
    
    trainer = CICDDoS2019Trainer(config_path=args.config)
    trainer.train(dataset_path=args.dataset, output_dir=args.output)


if __name__ == "__main__":
    main()
