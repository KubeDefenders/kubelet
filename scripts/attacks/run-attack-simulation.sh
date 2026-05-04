#!/bin/bash#!/bin/bash



# Main attack simulation launcher with menu interface# Orchestrate Complete Attack Simulation

# Provides easy access to all attack scenarios# This script runs a complete crossfire attack simulation with monitoring



set -eset -e



# Colors for outputecho "======================================"

RED='\033[0;31m'echo "Crossfire Attack Simulation Orchestrator"

GREEN='\033[0;32m'echo "======================================"

YELLOW='\033[1;33m'echo ""

BLUE='\033[0;34m'

CYAN='\033[0;36m'# Configuration

NC='\033[0m' # No ColorATTACK_TYPE=${1:-"app"}  # app or network

DURATION=${2:-60}

# Script directoryWORKERS=${3:-10}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"RATE=${4:-10}



clear# Colors

RED='\033[0;31m'

echo -e "${BLUE}╔════════════════════════════════════════════════════════════════╗${NC}"GREEN='\033[0;32m'

echo -e "${BLUE}║                                                                ║${NC}"YELLOW='\033[1;33m'

echo -e "${BLUE}║       ${CYAN}Crossfire DDoS Attack Simulation${BLUE}                       ║${NC}"NC='\033[0m' # No Color

echo -e "${BLUE}║                                                                ║${NC}"

echo -e "${BLUE}╚════════════════════════════════════════════════════════════════╝${NC}"# Check prerequisites

echo ""echo "[1/6] Checking prerequisites..."



# Check prerequisitesif ! command -v kubectl &> /dev/null; then

check_prerequisites() {    echo -e "${RED}Error: kubectl not found${NC}"

    local all_ok=true    exit 1

    fi

    echo -e "${YELLOW}Checking prerequisites...${NC}"

    echo ""if ! kubectl cluster-info &> /dev/null; then

        echo -e "${RED}Error: Kubernetes cluster not running${NC}"

    # Check kubectl    exit 1

    if command -v kubectl &> /dev/null; thenfi

        echo -e "  ${GREEN}✓${NC} kubectl installed"

    elseif ! kubectl get namespace sock-shop &> /dev/null; then

        echo -e "  ${RED}✗${NC} kubectl not found"    echo -e "${RED}Error: sock-shop namespace not found${NC}"

        all_ok=false    echo "Deploy Sock Shop first: ./scripts/deploy-sock-shop.sh"

    fi    exit 1

    fi

    # Check Kubernetes cluster

    if kubectl cluster-info &> /dev/null; thenecho -e "${GREEN}✓ Prerequisites met${NC}"

        echo -e "  ${GREEN}✓${NC} Kubernetes cluster running"

    else# Get application URL

        echo -e "  ${RED}✗${NC} Kubernetes cluster not accessible"echo ""

        all_ok=falseecho "[2/6] Getting application URL..."

    fi

    # Try to get ingress

    # Check sock-shop namespaceINGRESS_HOST=$(minikube ip 2>/dev/null || echo "localhost")

    if kubectl get namespace sock-shop &> /dev/null; thenINGRESS_PORT=$(kubectl -n istio-system get service istio-ingressgateway -o jsonpath='{.spec.ports[?(@.name=="http2")].nodePort}' 2>/dev/null || echo "8080")

        echo -e "  ${GREEN}✓${NC} Sock Shop namespace exists"APP_URL="http://${INGRESS_HOST}:${INGRESS_PORT}"

    else

        echo -e "  ${RED}✗${NC} Sock Shop not deployed"echo "Application URL: $APP_URL"

        all_ok=false

    fi# Verify app is reachable

    if curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 "$APP_URL" | grep -q "200\|301\|302"; then

    # Check Python3    echo -e "${GREEN}✓ Application is reachable${NC}"

    if command -v python3 &> /dev/null; thenelse

        echo -e "  ${GREEN}✓${NC} Python3 installed"    echo -e "${YELLOW}⚠ Application may not be reachable at $APP_URL${NC}"

    else    echo "You may need to run: kubectl port-forward -n sock-shop svc/front-end 8080:80"

        echo -e "  ${RED}✗${NC} Python3 not found"fi

        all_ok=false

    fi# Start monitoring

    echo ""

    # Check if pods are runningecho "[3/6] Starting monitoring tools..."

    local running_pods=$(kubectl get pods -n sock-shop --field-selector=status.phase=Running 2>/dev/null | wc -l)echo "Opening monitoring dashboards in background..."

    if [ "$running_pods" -gt 5 ]; then

        echo -e "  ${GREEN}✓${NC} Sock Shop pods running ($((running_pods-1)) pods)"# Start port forwards in background

    elsekubectl port-forward -n istio-system svc/kiali 20001:20001 > /dev/null 2>&1 &

        echo -e "  ${YELLOW}⚠${NC} Some Sock Shop pods may not be running"KIALI_PID=$!

    fisleep 2

    

    echo ""kubectl port-forward -n istio-system svc/grafana 3000:3000 > /dev/null 2>&1 &

    GRAFANA_PID=$!

    if [ "$all_ok" = false ]; thensleep 2

        echo -e "${RED}Please fix the issues above before running attacks.${NC}"

        echo ""kubectl port-forward -n istio-system svc/prometheus 9090:9090 > /dev/null 2>&1 &

        exit 1PROMETHEUS_PID=$!

    fisleep 2

}

echo -e "${GREEN}✓ Monitoring tools available:${NC}"

# Show menuecho "  Kiali:      http://localhost:20001"

show_menu() {echo "  Grafana:    http://localhost:3000"

    echo -e "${CYAN}Available Scenarios:${NC}"echo "  Prometheus: http://localhost:9090"

    echo ""

    echo -e "  ${GREEN}1${NC}) ${YELLOW}Endpoint Discovery${NC}"# Baseline metrics

    echo -e "     Automatically discover and map HTTP endpoints"echo ""

    echo ""echo "[4/6] Collecting baseline metrics (30 seconds)..."

    echo -e "  ${GREEN}2${NC}) ${YELLOW}Application-Level Attack${NC}"echo "Please observe normal traffic in monitoring dashboards..."

    echo -e "     HTTP flood with intelligent decoy link selection"sleep 30

    echo ""

    echo -e "  ${GREEN}3${NC}) ${YELLOW}Network-Level Attack${NC}"# Run attack

    echo -e "     Low-level packet flooding (requires root)"echo ""

    echo ""echo "[5/6] Launching attack..."

    echo -e "  ${GREEN}4${NC}) ${YELLOW}Complete Attack Scenario${NC}"echo ""

    echo -e "     Discovery → App Attack → Network Attack"echo -e "${RED}╔═══════════════════════════════════════════════════════════╗${NC}"

    echo ""echo -e "${RED}║           ATTACK SIMULATION STARTING                      ║${NC}"

    echo -e "  ${GREEN}5${NC}) ${YELLOW}Access Monitoring Dashboards${NC}"echo -e "${RED}╚═══════════════════════════════════════════════════════════╝${NC}"

    echo -e "     Open Grafana, Kiali, Prometheus"echo ""

    echo ""echo "Attack Type: $ATTACK_TYPE"

    echo -e "  ${GREEN}6${NC}) ${YELLOW}Show Current Status${NC}"echo "Duration: ${DURATION}s"

    echo -e "     Display system and pod status"echo "Workers/Threads: $WORKERS"

    echo ""echo "Rate: $RATE req/s or pkt/s"

    echo -e "  ${GREEN}0${NC}) ${RED}Exit${NC}"echo ""

    echo ""

}if [ "$ATTACK_TYPE" == "app" ]; then

    python3 attack-simulations/crossfire-app-level.py \

# Show status        --url "$APP_URL" \

show_status() {        --duration "$DURATION" \

    echo -e "${CYAN}Current System Status:${NC}"        --rate "$RATE" \

    echo ""        --workers "$WORKERS"

    elif [ "$ATTACK_TYPE" == "network" ]; then

    # Minikube    echo -e "${YELLOW}⚠ Network-level attack requires root privileges${NC}"

    echo -e "${YELLOW}Minikube:${NC}"    sudo python3 attack-simulations/crossfire-network-level.py \

    if minikube status 2>/dev/null | grep -q "Running"; then        --duration "$DURATION" \

        echo -e "  ${GREEN}✓ Running${NC}"        --rate "$RATE" \

    else        --threads "$WORKERS"

        echo -e "  ${RED}✗ Not running${NC}"else

    fi    echo -e "${RED}Error: Invalid attack type. Use 'app' or 'network'${NC}"

        exit 1

    # Get target URLfi

    MINIKUBE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null)

    NODEPORT=$(kubectl get svc front-end -n sock-shop -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null)# Post-attack observation

    echo ""

    if [ -n "$MINIKUBE_IP" ] && [ -n "$NODEPORT" ]; thenecho "[6/6] Post-attack observation (30 seconds)..."

        echo -e "  Target URL: ${GREEN}http://${MINIKUBE_IP}:${NODEPORT}${NC}"echo "Observe recovery in monitoring dashboards..."

    fisleep 30

    echo ""

    # Cleanup

    # Sock Shop podsecho ""

    echo -e "${YELLOW}Sock Shop Pods:${NC}"echo "Cleaning up port forwards..."

    kubectl get pods -n sock-shop 2>/dev/null | head -15kill $KIALI_PID $GRAFANA_PID $PROMETHEUS_PID 2>/dev/null || true

    echo ""

    echo ""

    # Istio podsecho "======================================"

    echo -e "${YELLOW}Istio System Pods:${NC}"echo "Simulation Complete"

    kubectl get pods -n istio-system 2>/dev/null | grep -E "NAME|prometheus|grafana|kiali"echo "======================================"

    echo ""echo ""

    echo "Review the results in your monitoring tools."

    # Check for discovered endpointsecho "Export metrics from Prometheus for further analysis."

    if [ -f "attack-simulations/discovered-endpoints.json" ]; thenecho ""

        echo -e "${GREEN}✓${NC} Discovered endpoints file exists"echo "To run again:"

        local endpoint_count=$(python3 -c "import json; print(len(json.load(open('attack-simulations/discovered-endpoints.json'))['discovered_urls']))" 2>/dev/null || echo "?")echo "  Application-level: $0 app <duration> <workers> <rate>"

        echo -e "  Found ${GREEN}${endpoint_count}${NC} endpoints"echo "  Network-level:     $0 network <duration> <threads> <rate>"

    else
        echo -e "${YELLOW}⚠${NC} No discovered endpoints yet (run scenario 1)"
    fi
    echo ""
}

# Main menu loop
main() {
    check_prerequisites
    
    while true; do
        show_menu
        read -p "Select scenario (0-6): " choice
        echo ""
        
        case $choice in
            1)
                echo -e "${CYAN}Running Endpoint Discovery...${NC}"
                echo ""
                cd "$SCRIPT_DIR/.."
                ./scripts/run-scenario-discovery.sh
                echo ""
                read -p "Press Enter to continue..."
                clear
                ;;
            2)
                echo -e "${CYAN}Running Application-Level Attack...${NC}"
                echo ""
                read -p "Duration in seconds [300]: " duration
                duration=${duration:-300}
                read -p "Number of decoy links [100]: " decoys
                decoys=${decoys:-100}
                echo ""
                cd "$SCRIPT_DIR/.."
                ./scripts/run-scenario-app-attack.sh "$duration" "$decoys"
                echo ""
                read -p "Press Enter to continue..."
                clear
                ;;
            3)
                echo -e "${CYAN}Running Network-Level Attack...${NC}"
                echo ""
                read -p "Duration in seconds [300]: " duration
                duration=${duration:-300}
                read -p "Number of decoy links [100]: " decoys
                decoys=${decoys:-100}
                echo ""
                echo -e "${YELLOW}⚠️  This requires root privileges${NC}"
                cd "$SCRIPT_DIR/.."
                sudo ./scripts/run-scenario-network-attack.sh "$duration" "$decoys"
                echo ""
                read -p "Press Enter to continue..."
                clear
                ;;
            4)
                echo -e "${CYAN}Running Complete Attack Scenario...${NC}"
                echo ""
                read -p "Duration per attack [300]: " duration
                duration=${duration:-300}
                read -p "Number of decoy links [100]: " decoys
                decoys=${decoys:-100}
                echo ""
                cd "$SCRIPT_DIR/.."
                ./scripts/run-scenario-complete.sh "$duration" "$decoys"
                echo ""
                read -p "Press Enter to continue..."
                clear
                ;;
            5)
                echo -e "${CYAN}Starting Monitoring Dashboards...${NC}"
                echo ""
                cd "$SCRIPT_DIR/.."
                ./scripts/access-monitoring.sh
                ;;
            6)
                clear
                show_status
                read -p "Press Enter to continue..."
                clear
                ;;
            0)
                echo -e "${GREEN}Exiting...${NC}"
                exit 0
                ;;
            *)
                echo -e "${RED}Invalid choice. Please select 0-6.${NC}"
                echo ""
                sleep 2
                clear
                ;;
        esac
    done
}

# Run main menu
main
