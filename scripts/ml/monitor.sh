#!/bin/bash
"""Live Detection Monitor"""

LOG_FILE="${1:-ml-detection/detector.log}"
REFRESH_INTERVAL=2

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

clear
echo -e "${CYAN}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║           DDoS Detection Monitor - Live Feed                   ║${NC}"
echo -e "${CYAN}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${YELLOW}Log File:${NC} ${LOG_FILE}"
echo -e "${YELLOW}Refresh:${NC} ${REFRESH_INTERVAL}s"
echo -e "${YELLOW}Press Ctrl+C to exit${NC}"
echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

if [ ! -f "${LOG_FILE}" ]; then
    echo -e "${RED}Error: Log file not found: ${LOG_FILE}${NC}"
    echo -e "${YELLOW}Tip: Start the detector first:${NC}"
    echo -e "  python3 ml-detection/detector.py --prometheus-url http://localhost:9090"
    exit 1
fi

LAST_DETECTION_COUNT=0

while true; do
    clear
    echo -e "${CYAN}╔════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║           DDoS Detection Monitor - Live Feed                   ║${NC}"
    echo -e "${CYAN}╚════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "${YELLOW}Monitoring:${NC} ${LOG_FILE}"
    echo -e "${YELLOW}Updated:${NC} $(date '+%H:%M:%S')"
    echo ""
    
    TOTAL_DETECTIONS=$(grep -c "DDOS ATTACK DETECTED" "${LOG_FILE}" 2>/dev/null || echo "0")
    TOTAL_DETECTIONS=${TOTAL_DETECTIONS//[^0-9]/}
    TOTAL_DETECTIONS=${TOTAL_DETECTIONS:-0}
    
    TOTAL_CYCLES=$(grep -c "^.*Cycle [0-9]" "${LOG_FILE}" 2>/dev/null || echo "0")
    TOTAL_CYCLES=${TOTAL_CYCLES//[^0-9]/}
    TOTAL_CYCLES=${TOTAL_CYCLES:-0}
    
    if [ "${TOTAL_DETECTIONS}" -gt "${LAST_DETECTION_COUNT}" ] 2>/dev/null; then
        NEW_DETECTIONS=$((TOTAL_DETECTIONS - LAST_DETECTION_COUNT))
        echo -e "${RED}🚨 ${NEW_DETECTIONS} NEW DETECTION(S)! 🚨${NC}"
        echo ""
    fi
    
    LAST_DETECTION_COUNT=${TOTAL_DETECTIONS}
    
    echo -e "${CYAN}━━━ Statistics ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${GREEN}Total Detections:${NC} ${TOTAL_DETECTIONS}"
    echo -e "${GREEN}Monitoring Cycles:${NC} ${TOTAL_CYCLES}"
    echo ""
    
    echo -e "${CYAN}━━━ Latest Metrics (Last 3 cycles) ━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    tail -100 "${LOG_FILE}" | grep "Cycle [0-9]" | tail -3 | while read -r line; do
        if echo "$line" | grep -q "Baseline established"; then
            echo -e "${GREEN}$line${NC}"
        else
            echo -e "${BLUE}$line${NC}"
        fi
    done
    echo ""
    
    if [ "${TOTAL_DETECTIONS}" -gt 0 ] 2>/dev/null; then
        echo -e "${CYAN}━━━ Latest Detection ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        grep -A 20 "DDOS ATTACK DETECTED" "${LOG_FILE}" | tail -25 | head -20
        echo ""
    else
        echo -e "${CYAN}━━━ Status ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo -e "${GREEN}No attacks detected yet. System monitoring...${NC}"
        echo ""
    fi
    
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${YELLOW}Refreshing in ${REFRESH_INTERVAL}s... (Ctrl+C to exit)${NC}"
    
    sleep ${REFRESH_INTERVAL}
done
