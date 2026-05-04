#!/bin/bash
# Apply only network-level mitigations

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MITIGATIONS_DIR="$(dirname "$SCRIPT_DIR")"

echo "Applying Network-Level Mitigations Only..."
kubectl apply -f "$MITIGATIONS_DIR/network-level/"
echo "Done!"
