#!/bin/bash
# Unified Experiment Launcher
# Quick launcher for all experiment types

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default configuration
MODE="continuous"
DURATION=900  # 15 minutes
ATTACK_TYPE=""
ATTACK_DURATION=""

# Function to print colored output
print_info() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_error() {
    echo -e "${RED}✗ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

# Function to show usage
usage() {
    cat << EOF
Unified Experiment Launcher

Usage: ./launcher.sh [MODE] [OPTIONS]

Modes:
  continuous    Random attack injection with continuous monitoring (default)
  research      Structured research experiment
  single        Single attack test

Options:
  -d, --duration SECONDS    Duration for continuous mode (default: 900 = 15 min)
  -t, --attack-type TYPE    Attack type for single mode
  -a, --attack-dur SECONDS  Attack duration for single mode
  -u, --url URL            Target URL (auto-detected if not provided)
  -h, --help               Show this help

Examples:
  # 15-minute continuous experiment
  ./launcher.sh continuous

  # 30-minute continuous experiment
  ./launcher.sh continuous -d 1800

  # Research mode
  ./launcher.sh research

  # Single attack test
  ./launcher.sh single -t http-flood -a 120

EOF
    exit 0
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        continuous|research|single)
            MODE="$1"
            shift
            ;;
        -d|--duration)
            DURATION="$2"
            shift 2
            ;;
        -t|--attack-type)
            ATTACK_TYPE="$2"
            shift 2
            ;;
        -a|--attack-dur)
            ATTACK_DURATION="$2"
            shift 2
            ;;
        -u|--url)
            TARGET_URL="$2"
            shift 2
            ;;
        -h|--help)
            usage
            ;;
        *)
            print_error "Unknown option: $1"
            usage
            ;;
    esac
done

# Display header
echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  🧪 DDoS Experiment Launcher - ${MODE^^} Mode"
echo "═══════════════════════════════════════════════════════════════"
echo ""

# Check prerequisites
print_info "Checking prerequisites..."

# Check if minikube is running
if ! minikube status &> /dev/null; then
    print_error "Minikube is not running"
    echo "   Please start minikube: minikube start"
    exit 1
fi
print_success "Minikube is running"

# Auto-detect target URL if not provided
if [ -z "$TARGET_URL" ]; then
    print_info "Auto-detecting target URL..."
    MINIKUBE_IP=$(minikube ip)
    NODE_PORT=$(kubectl get svc front-end -n sock-shop -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null)
    
    if [ -z "$NODE_PORT" ]; then
        print_error "Cannot find front-end service in sock-shop namespace"
        echo "   Is sock-shop deployed?"
        exit 1
    fi
    
    TARGET_URL="http://${MINIKUBE_IP}:${NODE_PORT}"
    print_success "Target URL: $TARGET_URL"
else
    print_success "Using provided URL: $TARGET_URL"
fi

# Activate virtual environment
if [ -d "../.venv" ] && [ -z "$VIRTUAL_ENV" ]; then
    print_info "Activating virtual environment..."
    source ../.venv/bin/activate
    print_success "Virtual environment activated"
fi

# Display configuration
echo ""
echo "Configuration:"
echo "  Mode:      $MODE"
if [ "$MODE" = "continuous" ]; then
    echo "  Duration:  $(($DURATION / 60)) minutes (${DURATION} seconds)"
fi
if [ "$MODE" = "single" ]; then
    echo "  Attack:    $ATTACK_TYPE"
    echo "  Duration:  $ATTACK_DURATION seconds"
fi
echo "  Target:    $TARGET_URL"
echo "  Output:    ../results/experiments"
echo ""

# Confirm before starting
read -p "Press Enter to start experiment (Ctrl+C to cancel)..."

echo ""
print_info "Starting experiment..."
echo ""

# Run experiment based on mode
case $MODE in
    continuous)
        python3 experiment.py continuous \
            --target-url "$TARGET_URL" \
            --duration $DURATION
        ;;
    research)
        python3 experiment.py research \
            --target-url "$TARGET_URL"
        ;;
    single)
        if [ -z "$ATTACK_TYPE" ] || [ -z "$ATTACK_DURATION" ]; then
            print_error "Single mode requires --attack-type and --attack-dur"
            exit 1
        fi
        python3 experiment.py single \
            --target-url "$TARGET_URL" \
            --attack-type "$ATTACK_TYPE" \
            --duration $ATTACK_DURATION
        ;;
esac

# Check if experiment succeeded
if [ $? -eq 0 ]; then
    echo ""
    print_success "Experiment completed successfully!"
    echo ""
    print_info "To analyze results, run:"
    echo "    python3 analyze.py summary"
else
    echo ""
    print_error "Experiment failed"
    exit 1
fi
