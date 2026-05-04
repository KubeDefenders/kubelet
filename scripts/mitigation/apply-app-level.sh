#!/bin/bash
# Apply only application-level mitigations

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MITIGATIONS_DIR="$(dirname "$SCRIPT_DIR")"

echo "Applying Application-Level Mitigations Only..."
kubectl apply -f "$MITIGATIONS_DIR/app-level/"
echo "Done!"
