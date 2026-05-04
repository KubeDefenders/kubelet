#!/bin/bash
# Project Structure Verification Script
# Verifies the reorganized project structure is correct

set -e

echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║         PROJECT STRUCTURE VERIFICATION                        ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""

ERRORS=0

# Function to check directory exists
check_dir() {
    if [ -d "$1" ]; then
        echo "✓ $1"
    else
        echo "✗ $1 (MISSING)"
        ERRORS=$((ERRORS + 1))
    fi
}

# Function to check file exists
check_file() {
    if [ -f "$1" ]; then
        echo "✓ $1"
    else
        echo "✗ $1 (MISSING)"
        ERRORS=$((ERRORS + 1))
    fi
}

# Check main directories
echo "📁 Main Directories:"
check_dir "src"
check_dir "docs"
check_dir ".venv"
echo ""

# Check docs subdirectories
echo "📚 Documentation Structure:"
check_dir "docs/guides"
check_dir "docs/architecture"
check_dir "docs/research"
check_dir "docs/summaries"
check_dir "docs/setup"
echo ""

# Check essential files
echo "📄 Essential Files:"
check_file "README.md"
check_file "MIGRATION_GUIDE.md"
check_file "REORGANIZATION_SUMMARY.md"
check_file "docs/README.md"
echo ""

# Check source files
echo "💻 Source Code:"
check_file "src/run_continuous_experiment.py"
check_file "src/run_15min_experiment.sh"
check_file "src/summarize_results.py"
check_file "src/traffic-generator.py"
echo ""

# Check no duplicate venvs
echo "🔍 Checking for duplicate virtual environments:"
if [ -d "venv" ]; then
    echo "✗ venv/ should be removed"
    ERRORS=$((ERRORS + 1))
else
    echo "✓ venv/ removed"
fi

if [ -d "attack-simulations/venv" ]; then
    echo "✗ attack-simulations/venv/ should be removed"
    ERRORS=$((ERRORS + 1))
else
    echo "✓ attack-simulations/venv/ removed"
fi
echo ""

# Count files
echo "📊 Statistics:"
DOC_COUNT=$(find docs -name "*.md" 2>/dev/null | wc -l)
SRC_COUNT=$(ls src/*.py src/*.sh 2>/dev/null | wc -l)
ROOT_MD=$(ls *.md 2>/dev/null | wc -l)

echo "  Documentation files: $DOC_COUNT (should be ~37)"
echo "  Source files in src/: $SRC_COUNT (should be ~13)"
echo "  Markdown in root: $ROOT_MD (should be 2-3)"
echo ""

# Final result
echo "═══════════════════════════════════════════════════════════════"
if [ $ERRORS -eq 0 ]; then
    echo "✅ All checks passed! Project structure is correct."
    echo "═══════════════════════════════════════════════════════════════"
    exit 0
else
    echo "❌ Found $ERRORS error(s). Please review structure."
    echo "═══════════════════════════════════════════════════════════════"
    exit 1
fi
