#!/bin/bash
# Setup script for ml-optimized-detector dataset

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
DATA_DIR="$SCRIPT_DIR/data"
DATASET_DIR="$DATA_DIR/cicddos2019"

echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║     ML-Optimized DDoS Detector - Dataset Setup               ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""

# Check if data directory exists
if [ ! -d "$DATA_DIR" ]; then
    echo "Creating data directory..."
    mkdir -p "$DATA_DIR"
fi

# Check if dataset already exists
if [ -d "$DATASET_DIR" ] || [ -L "$DATASET_DIR" ]; then
    echo "✅ Dataset directory already exists: $DATASET_DIR"
    
    # Check if it has files
    if [ -n "$(ls -A $DATASET_DIR 2>/dev/null)" ]; then
        FILE_COUNT=$(ls -1 $DATASET_DIR | wc -l)
        echo "   Found $FILE_COUNT files in dataset directory"
        echo ""
        echo "Dataset is ready! You can proceed with training:"
        echo "  python train_detector.py --dataset data/cicddos2019 --output models/"
        exit 0
    else
        echo "⚠️  Dataset directory exists but is empty"
    fi
fi

echo ""
echo "Dataset not found. Please choose an option:"
echo ""
echo "1) Create symlink to existing dataset"
echo "2) Create empty directory (manual download)"
echo "3) Exit"
echo ""
read -p "Enter choice [1-3]: " choice

case $choice in
    1)
        echo ""
        read -p "Enter path to existing CICDDoS2019 dataset: " existing_path
        
        # Expand tilde and relative paths
        existing_path=$(eval echo "$existing_path")
        
        if [ ! -d "$existing_path" ]; then
            echo "❌ Error: Directory not found: $existing_path"
            exit 1
        fi
        
        # Check if it has files
        if [ -z "$(ls -A $existing_path 2>/dev/null)" ]; then
            echo "⚠️  Warning: Directory exists but is empty: $existing_path"
            read -p "Continue anyway? [y/N]: " confirm
            if [[ ! $confirm =~ ^[Yy]$ ]]; then
                exit 1
            fi
        fi
        
        echo "Creating symlink..."
        ln -s "$existing_path" "$DATASET_DIR"
        echo "✅ Symlink created: $DATASET_DIR -> $existing_path"
        echo ""
        echo "Dataset is ready! You can proceed with training:"
        echo "  python train_detector.py --dataset data/cicddos2019 --output models/"
        ;;
        
    2)
        echo ""
        echo "Creating empty directory..."
        mkdir -p "$DATASET_DIR"
        echo "✅ Directory created: $DATASET_DIR"
        echo ""
        echo "📥 Next steps:"
        echo "   1. Download CICDDoS2019 dataset from:"
        echo "      https://www.unb.ca/cic/datasets/ddos-2019.html"
        echo ""
        echo "   2. Extract files to: $DATASET_DIR"
        echo ""
        echo "   3. Verify files:"
        echo "      ls -lh $DATASET_DIR"
        echo ""
        echo "   4. Train models:"
        echo "      python train_detector.py --dataset data/cicddos2019 --output models/"
        echo ""
        echo "For detailed instructions, see: DATASET_SETUP.md"
        ;;
        
    3)
        echo "Exiting..."
        exit 0
        ;;
        
    *)
        echo "❌ Invalid choice"
        exit 1
        ;;
esac
