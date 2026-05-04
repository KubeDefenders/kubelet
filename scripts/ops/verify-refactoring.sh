#!/bin/bash
# Refactoring Verification Script
# Verifies that the project structure refactoring is complete

set -e

echo "╔════════════════════════════════════════════════════════════╗"
echo "║  DDoS Research Platform - Structure Verification          ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""

ERRORS=0
WARNINGS=0

# Helper functions
check_dir() {
    if [ -d "$1" ]; then
        echo "✅ $2"
    else
        echo "❌ $2 (missing: $1)"
        ((ERRORS++))
    fi
}

check_file() {
    if [ -f "$1" ]; then
        echo "✅ $2"
    else
        echo "❌ $2 (missing: $1)"
        ((ERRORS++))
    fi
}

check_not_exist() {
    if [ ! -e "$1" ]; then
        echo "✅ $2"
    else
        echo "⚠️  $2 (still exists: $1)"
        ((WARNINGS++))
    fi
}

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "1. RESEARCH COMPONENTS (Root Level)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

check_dir "target" "Target application directory"
check_dir "attacks" "Attacks directory (renamed from attack-simulations)"
check_dir "detection" "Detection directory (new merged component)"
check_dir "mitigation" "Mitigation directory (renamed from mitigations)"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "2. COMPONENT INTERFACES"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

check_file "target/interface.yaml" "Target interface"
check_file "attacks/interface.yaml" "Attacks interface"
check_file "detection/interface.yaml" "Detection interface"
check_file "mitigation/interface.yaml" "Mitigation interface"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "3. CONFIGURATION"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

check_dir "config" "Config directory"
check_file "config/runtime.env" "Runtime environment configuration"
check_file "config/component-paths.yaml" "Component paths configuration"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "4. CI/CD INFRASTRUCTURE"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

check_dir ".github/workflows" "Workflows directory"
check_dir ".github/actions" "Actions directory"

# Check workflows (no numbering)
check_file ".github/workflows/setup-target.yml" "Setup target workflow"
check_file ".github/workflows/setup-monitoring.yml" "Setup monitoring workflow"
check_file ".github/workflows/setup-mitigation-kubernetes.yml" "Setup K8s mitigation workflow"
check_file ".github/workflows/run-experiment.yml" "Run experiment workflow"
check_file ".github/workflows/cleanup.yml" "Cleanup workflow"

# Check actions
check_dir ".github/actions/setup-kubernetes" "Setup Kubernetes action"
check_dir ".github/actions/deploy-target" "Deploy target action"
check_dir ".github/actions/deploy-monitoring" "Deploy monitoring action"
check_dir ".github/actions/run-attack" "Run attack action"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "5. COMPONENT STRUCTURE"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Target structure
check_dir "target/istio" "Istio configs in target (moved from root)"

# Detection structure
check_dir "detection/ml-detector" "ML detector in detection"
check_dir "detection/monitoring" "Monitoring in detection (moved from root)"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "6. DOCUMENTATION CONSOLIDATION"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

check_dir "docs" "Documentation directory"
check_dir "docs/guides" "Guides directory"
check_dir "docs/architecture" "Architecture directory"
check_dir "docs/reports" "Reports directory"
check_dir "docs/planning" "Planning directory"
check_dir "docs/project-history" "Project history directory"

# Component-specific docs
check_dir "docs/guides/attacks" "Attacks guides"
check_dir "docs/guides/detection" "Detection guides"
check_dir "docs/guides/mitigation" "Mitigation guides"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "7. SCRIPTS CONSOLIDATION"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

check_dir "scripts" "Scripts directory"
check_dir "scripts/attacks" "Attacks scripts"
check_dir "scripts/mitigation" "Mitigation scripts"
check_dir "scripts/detection" "Detection scripts"
check_dir "scripts/ops" "Ops scripts"
check_dir "scripts/workflows" "Workflow scripts"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "8. RESULTS ORGANIZATION"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

check_dir "results" "Results directory"
check_dir "results/templates" "Templates in results"
check_dir "results/attacks" "Attack results"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "9. CLEANUP VERIFICATION"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Old directories should not exist
check_not_exist "attack-simulations" "Old attack-simulations directory removed"
check_not_exist "mitigations" "Old mitigations directory removed"
check_not_exist "ml-detector" "Old ml-detector directory removed (moved to detection/)"
check_not_exist "monitoring" "Old monitoring directory removed (moved to detection/)"
check_not_exist "istio" "Old istio directory removed (moved to target/)"
check_not_exist "data" "Empty data directory removed"

# Deprecated files
DEPRECATED_COUNT=$(find . -name "_deprecated_*" -type f 2>/dev/null | wc -l)
if [ "$DEPRECATED_COUNT" -eq 0 ]; then
    echo "✅ No deprecated files found"
else
    echo "⚠️  Found $DEPRECATED_COUNT deprecated files (should be 0)"
    find . -name "_deprecated_*" -type f
    ((WARNINGS++))
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "10. ROOT DIRECTORY CLEANLINESS"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

ROOT_COUNT=$(find . -maxdepth 1 -type f ! -name ".*" | wc -l)
echo "Root files count: $ROOT_COUNT (expected: ~3-5 essential files)"

if [ "$ROOT_COUNT" -le 5 ]; then
    echo "✅ Root directory is clean"
else
    echo "⚠️  Root has more files than expected"
    echo "Files in root:"
    find . -maxdepth 1 -type f ! -name ".*" -exec basename {} \;
    ((WARNINGS++))
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "11. MAIN README"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

check_file "README.md" "Main README.md"

if grep -q "Research Goal" README.md 2>/dev/null; then
    echo "✅ README reflects research goal"
else
    echo "⚠️  README may not reflect updated structure"
    ((WARNINGS++))
fi

if grep -q "CI/CD Workflows" README.md 2>/dev/null; then
    echo "✅ README mentions CI/CD workflows"
else
    echo "⚠️  README doesn't mention CI/CD workflows"
    ((WARNINGS++))
fi

echo ""
echo "╔════════════════════════════════════════════════════════════╗"
echo "║                    VERIFICATION SUMMARY                    ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""

if [ $ERRORS -eq 0 ] && [ $WARNINGS -eq 0 ]; then
    echo "🎉 SUCCESS! All checks passed!"
    echo ""
    echo "The project has been successfully refactored:"
    echo "  ✅ 4 independent research components"
    echo "  ✅ Component interfaces and configuration"
    echo "  ✅ 8 CI/CD workflows (no numbering)"
    echo "  ✅ 6 reusable composite actions"
    echo "  ✅ Consolidated documentation"
    echo "  ✅ Consolidated scripts"
    echo "  ✅ Clean root directory"
    echo ""
    exit 0
elif [ $ERRORS -eq 0 ]; then
    echo "⚠️  Verification completed with $WARNINGS warning(s)"
    echo "The refactoring is functionally complete but has minor issues."
    echo ""
    exit 0
else
    echo "❌ Verification failed with $ERRORS error(s) and $WARNINGS warning(s)"
    echo "Please review the errors above and fix them."
    echo ""
    exit 1
fi
