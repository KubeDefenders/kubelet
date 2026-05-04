#!/bin/bash
# Deploy Istio Advanced DDoS Mitigations
# Rate limiting, circuit breaking, traffic shifting, fault injection

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

NAMESPACE="sock-shop"
MITIGATION_DIR="/home/spuggle/dev/ddos/mitigations/istio-advanced"

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Istio Advanced DDoS Mitigation Deployment${NC}"
echo -e "${GREEN}========================================${NC}"

# Check prerequisites
if ! command -v kubectl &> /dev/null; then
    echo -e "${RED}Error: kubectl not found${NC}"
    exit 1
fi

if ! kubectl cluster-info &> /dev/null; then
    echo -e "${RED}Error: Cannot connect to cluster${NC}"
    exit 1
fi

# Check if Istio is installed
if ! kubectl get namespace istio-system &> /dev/null; then
    echo -e "${RED}Error: Istio is not installed. Please install Istio first.${NC}"
    echo -e "${YELLOW}Install guide: https://istio.io/latest/docs/setup/getting-started/${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Istio is installed${NC}"

# Check if sock-shop namespace has Istio injection enabled
INJECTION_ENABLED=$(kubectl get namespace $NAMESPACE -o jsonpath='{.metadata.labels.istio-injection}' 2>/dev/null || echo "")
if [ "$INJECTION_ENABLED" != "enabled" ]; then
    echo -e "${YELLOW}⚠ Istio injection not enabled for $NAMESPACE. Enabling...${NC}"
    kubectl label namespace $NAMESPACE istio-injection=enabled --overwrite
    echo -e "${GREEN}✓ Istio injection enabled${NC}"
    echo -e "${YELLOW}Note: Restart pods for injection to take effect:${NC}"
    echo -e "${YELLOW}  kubectl rollout restart deployment -n $NAMESPACE${NC}"
fi

apply_config() {
    local config_path=$1
    local description=$2
    
    echo ""
    echo -e "${YELLOW}Deploying: $description${NC}"
    
    if [ -f "$config_path" ]; then
        if kubectl apply -f "$config_path"; then
            echo -e "${GREEN}✓ $description applied${NC}"
        else
            echo -e "${RED}✗ Failed to apply $description${NC}"
            return 1
        fi
    else
        echo -e "${RED}✗ File not found: $config_path${NC}"
        return 1
    fi
}

# 1. Deploy Rate Limiting
echo ""
echo -e "${GREEN}======================================${NC}"
echo -e "${GREEN}Step 1: Deploying Rate Limiting${NC}"
echo -e "${GREEN}======================================${NC}"

echo -e "${YELLOW}Deploying global rate limiting (Redis + rate limit service)...${NC}"
apply_config "$MITIGATION_DIR/rate-limiting/global-rate-limit.yaml" "Global Rate Limiting"

echo -e "${YELLOW}Waiting for Redis to be ready...${NC}"
kubectl wait --for=condition=available --timeout=120s deployment/redis -n $NAMESPACE || true

echo -e "${YELLOW}Waiting for rate limit service to be ready...${NC}"
kubectl wait --for=condition=available --timeout=120s deployment/ratelimit -n $NAMESPACE || true

echo -e "${YELLOW}Deploying local rate limiting (EnvoyFilter)...${NC}"
apply_config "$MITIGATION_DIR/rate-limiting/local-rate-limit.yaml" "Local Rate Limiting"

# 2. Deploy Circuit Breaking
echo ""
echo -e "${GREEN}======================================${NC}"
echo -e "${GREEN}Step 2: Deploying Circuit Breakers${NC}"
echo -e "${GREEN}======================================${NC}"
apply_config "$MITIGATION_DIR/circuit-breaking/circuit-breaker-rules.yaml" "Circuit Breaker DestinationRules"

# 3. Deploy Traffic Shifting (optional, for testing)
echo ""
echo -e "${GREEN}======================================${NC}"
echo -e "${GREEN}Step 3: Deploying Traffic Management${NC}"
echo -e "${GREEN}======================================${NC}"

read -p "Deploy traffic shifting configurations? (y/N) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    apply_config "$MITIGATION_DIR/traffic-shifting/timeout-retry-policies.yaml" "Timeout and Retry Policies"
    
    read -p "Deploy canary configurations? (y/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        apply_config "$MITIGATION_DIR/traffic-shifting/canary-deployments.yaml" "Canary Deployments"
    fi
else
    echo -e "${YELLOW}Skipping traffic shifting${NC}"
fi

# 4. Deploy Fault Injection (chaos testing, optional)
echo ""
echo -e "${GREEN}======================================${NC}"
echo -e "${GREEN}Step 4: Fault Injection (Optional)${NC}"
echo -e "${GREEN}======================================${NC}"

read -p "Deploy fault injection for chaos testing? (y/N) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo -e "${YELLOW}⚠ WARNING: Fault injection will intentionally degrade services${NC}"
    read -p "Are you sure? (y/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        apply_config "$MITIGATION_DIR/fault-injection/chaos-testing.yaml" "Fault Injection Rules"
    fi
else
    echo -e "${YELLOW}Skipping fault injection${NC}"
fi

# Verification
echo ""
echo -e "${GREEN}======================================${NC}"
echo -e "${GREEN}Verification${NC}"
echo -e "${GREEN}======================================${NC}"

echo ""
echo -e "${YELLOW}Checking DestinationRules (circuit breakers):${NC}"
kubectl get destinationrules -n $NAMESPACE

echo ""
echo -e "${YELLOW}Checking VirtualServices:${NC}"
kubectl get virtualservices -n $NAMESPACE

echo ""
echo -e "${YELLOW}Checking EnvoyFilters:${NC}"
kubectl get envoyfilters -n $NAMESPACE

echo ""
echo -e "${YELLOW}Checking rate limiting pods:${NC}"
kubectl get pods -n $NAMESPACE -l app=redis
kubectl get pods -n $NAMESPACE -l app=ratelimit

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Deployment Complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "${YELLOW}Testing Rate Limiting:${NC}"
echo "# From inside a pod:"
echo "for i in {1..200}; do curl -s -o /dev/null -w \"%{http_code}\n\" http://front-end:8079/; done"
echo "# Should see some 429 (Too Many Requests) responses"
echo ""
echo -e "${YELLOW}Testing Circuit Breaker:${NC}"
echo "kubectl exec -it deploy/front-end -n $NAMESPACE -- /bin/sh"
echo "# Generate errors to trip circuit breaker:"
echo "for i in {1..20}; do curl http://catalogue/fake-error-endpoint; done"
echo ""
echo -e "${YELLOW}Monitoring:${NC}"
echo "kubectl logs -l app=ratelimit -n $NAMESPACE -f"
echo "kubectl logs -l app=redis -n $NAMESPACE -f"
echo ""
