#!/bin/bash

# Run complete crossfire attack scenario:
# 1. Discovery
# 2. Application-level attack
# 3. Network-level attack

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default values
DURATION="${1:-300}"
DECOY_COUNT="${2:-100}"

echo -e "${BLUE}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║     Complete Crossfire DDoS Attack Scenario                   ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""

echo -e "${GREEN}This scenario will execute:${NC}"
echo -e "  1. ${YELLOW}Endpoint Discovery${NC} - Map the target application"
echo -e "  2. ${YELLOW}Application-Level Attack${NC} - HTTP flood with decoy links"
echo -e "  3. ${YELLOW}Network-Level Attack${NC} - Low-level packet flooding"
echo ""
echo -e "${GREEN}Attack Duration:${NC} ${DURATION} seconds per attack"
echo -e "${GREEN}Decoy Count:${NC} ${DECOY_COUNT} decoy links"
echo ""

read -p "Press Enter to begin the complete scenario (Ctrl+C to cancel)..."
echo ""

# Phase 1: Discovery
echo -e "${BLUE}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  Phase 1: Endpoint Discovery                                   ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""

./scripts/run-scenario-discovery.sh

if [ $? -ne 0 ]; then
    echo -e "${RED}Discovery failed. Aborting scenario.${NC}"
    exit 1
fi

echo ""
echo -e "${GREEN}✓ Phase 1 Complete${NC}"
echo -e "Waiting 10 seconds before next phase..."
sleep 10
echo ""

# Phase 2: Application-Level Attack
echo -e "${BLUE}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  Phase 2: Application-Level Attack                             ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""

./scripts/run-scenario-app-attack.sh "$DURATION" "$DECOY_COUNT"

if [ $? -ne 0 ]; then
    echo -e "${RED}Application-level attack failed.${NC}"
    exit 1
fi

echo ""
echo -e "${GREEN}✓ Phase 2 Complete${NC}"
echo -e "Waiting 30 seconds for system to stabilize..."
sleep 30
echo ""

# Phase 3: Network-Level Attack
echo -e "${BLUE}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  Phase 3: Network-Level Attack                                 ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""

echo -e "${YELLOW}⚠️  This phase requires root privileges${NC}"
echo -e "You may be prompted for your password."
echo ""

sudo ./scripts/run-scenario-network-attack.sh "$DURATION" "$DECOY_COUNT"

if [ $? -ne 0 ]; then
    echo -e "${RED}Network-level attack failed.${NC}"
    exit 1
fi

echo ""
echo -e "${GREEN}✓ Phase 3 Complete${NC}"
echo ""

# Final summary
echo -e "${BLUE}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  Complete Scenario Finished                                    ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${GREEN}All attack phases completed successfully!${NC}"
echo ""
echo -e "${BLUE}Review the results:${NC}"
echo -e "  • Discovery data: ${GREEN}attack-simulations/discovered-endpoints.json${NC}"
echo -e "  • Grafana: ${GREEN}kubectl port-forward -n istio-system svc/grafana 3000:3000${NC}"
echo -e "  • Kiali: ${GREEN}kubectl port-forward -n istio-system svc/kiali 20001:20001${NC}"
echo ""
