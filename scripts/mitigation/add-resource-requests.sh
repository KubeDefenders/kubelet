#!/bin/bash
# Add resource requests to sock-shop deployments to enable HPA
# HPAs require resource requests to calculate percentage-based targets

set -e

NAMESPACE="sock-shop"

echo "========================================="
echo "Adding Resource Requests to Deployments"
echo "========================================="

# Function to add resource requests to a deployment
add_resources() {
    local deployment=$1
    local cpu_request=$2
    local memory_request=$3
    local cpu_limit=$4
    local memory_limit=$5
    
    echo "Patching $deployment..."
    
    kubectl patch deployment "$deployment" -n "$NAMESPACE" --type='json' -p="[
  {
    \"op\": \"add\",
    \"path\": \"/spec/template/spec/containers/0/resources\",
    \"value\": {
      \"requests\": {
        \"cpu\": \"$cpu_request\",
        \"memory\": \"$memory_request\"
      },
      \"limits\": {
        \"cpu\": \"$cpu_limit\",
        \"memory\": \"$memory_limit\"
      }
    }
  }
]" 2>/dev/null || kubectl set resources deployment "$deployment" -n "$NAMESPACE" \
    --requests=cpu="$cpu_request",memory="$memory_request" \
    --limits=cpu="$cpu_limit",memory="$memory_limit"
    
    if [ $? -eq 0 ]; then
        echo "✓ $deployment patched, waiting for rollout..."
        kubectl rollout status deployment/"$deployment" -n "$NAMESPACE" --timeout=60s || {
            echo "⚠ Rollout timeout for $deployment, continuing anyway"
        }
    else
        echo "⚠ Failed to patch $deployment"
    fi
}

# Add resource requests to key services
# Format: deployment cpu_request memory_request cpu_limit memory_limit

# Front-end (main target) - needs enough to show scaling
add_resources "front-end" "50m" "128Mi" "500m" "512Mi"

# Catalogue (decoy) - lower resources to trigger HPA faster
add_resources "catalogue" "30m" "64Mi" "300m" "256Mi"

# Carts (decoy)
add_resources "carts" "30m" "128Mi" "300m" "512Mi"

# Orders - moderate resources
add_resources "orders" "50m" "256Mi" "500m" "1Gi"

# Payment
add_resources "payment" "30m" "64Mi" "300m" "256Mi"

# User
add_resources "user" "30m" "64Mi" "300m" "256Mi"

echo ""
echo "✓ All resource requests added successfully!"
echo ""
echo "Verify with:"
echo "  kubectl get hpa -n $NAMESPACE"
echo "  kubectl top pods -n $NAMESPACE"
