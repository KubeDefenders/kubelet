#!/bin/bash
# Remove all mitigations

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MITIGATIONS_DIR="$(dirname "$SCRIPT_DIR")"

echo "Removing all mitigations..."
kubectl delete -f "$MITIGATIONS_DIR/app-level/" 2>/dev/null || true
kubectl delete -f "$MITIGATIONS_DIR/network-level/" 2>/dev/null || true
echo "Done!"
