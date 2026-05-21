#!/usr/bin/env bash
#
# Deploy Nephio-integrated KubeDDoS
# Applies the CRD, starts the kopf controller, applies the intent CR,
# and waits for the controller to generate concrete K8s resources.
#
# The controller (nephio_controller.py) reads spec.intent at runtime and
# generates NetworkPolicies, HPAs, and ResourceQuotas dynamically —
# in contrast to the plain KubeDDoS scenario which applies pre-authored
# translated/ manifests.
#
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../../" && pwd)"
CONTROLLER="$SCRIPT_DIR/controller/nephio_controller.py"
INTENT_CR="$SCRIPT_DIR/controller/intent-cr.yaml"
CRD_FILE="$SCRIPT_DIR/workload-apis/ddos-protection-crds.yaml"

# Python / kopf executable — use project venv if present
PYTHON="${PROJECT_ROOT}/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
    PYTHON="$(which python3)"
fi

KOPF_PID_FILE="/tmp/nephio-controller.pid"

cleanup_controller() {
    if [ -f "$KOPF_PID_FILE" ]; then
        local pid
        pid=$(cat "$KOPF_PID_FILE")
        echo "  Stopping Nephio controller (PID $pid)..."
        kill "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
        rm -f "$KOPF_PID_FILE"
    fi
}

stop_controller() {
    cleanup_controller
    echo "  Controller stopped."
}

# ── 1. Ensure kopf is installed ──────────────────────────────────────────────
echo "  Checking kopf installation..."
if ! "$PYTHON" -c "import kopf" 2>/dev/null; then
    echo "  Installing kopf and kubernetes packages..."
    "$PYTHON" -m pip install --quiet "kopf>=1.37.0" "kubernetes>=28.0.0"
fi

# ── 2. Apply the CRD ─────────────────────────────────────────────────────────
echo "  Applying DDoSProtection CRD..."
kubectl apply -f "$CRD_FILE"

# ── 3. Remove any leftover resources from previous scenario ──────────────────
echo "  Cleaning up leftover mitigations..."
kubectl delete networkpolicies,hpa,resourcequotas --all -n sock-shop \
    --ignore-not-found=true 2>/dev/null || true

# Force-remove kopf finalizer so delete is not blocked by a stale controller
for cr in $(kubectl get ddosprotection -n sock-shop -o name 2>/dev/null); do
    kubectl patch "$cr" -n sock-shop --type=json \
        -p='[{"op":"remove","path":"/metadata/finalizers"}]' 2>/dev/null || true
done
kubectl delete ddosprotection --all -n sock-shop --ignore-not-found=true 2>/dev/null || true

# Wait until the CR is fully gone before starting a fresh kopf instance
cr_wait=0
while [ $cr_wait -lt 15 ]; do
    count=$(kubectl get ddosprotection -n sock-shop --no-headers 2>/dev/null | wc -l)
    [ "$count" -eq 0 ] && break
    sleep 2
    cr_wait=$((cr_wait + 2))
done
sleep 3

# ── 4. Start the kopf controller in the background ───────────────────────────
echo "  Starting Nephio controller (kopf)..."
cleanup_controller  # kill any stale instance

"$PYTHON" -m kopf run \
    --all-namespaces \
    --verbose \
    "$CONTROLLER" \
    > /tmp/nephio-controller.log 2>&1 &

echo $! > "$KOPF_PID_FILE"
echo "  Controller PID: $(cat $KOPF_PID_FILE) (log: /tmp/nephio-controller.log)"

# Give the controller a moment to start watching
sleep 5

# ── 5. Apply the intent CR (controller reconciles it) ────────────────────────
echo "  Applying DDoSProtection intent CR..."
kubectl apply -f "$INTENT_CR"

echo "  Waiting for controller to reconcile resources..."

# Poll until the controller has created at least one HPA and one NetworkPolicy
local_timeout=60
elapsed=0
while [ $elapsed -lt $local_timeout ]; do
    hpa_count=$(kubectl get hpa -n sock-shop --no-headers 2>/dev/null | wc -l)
    np_count=$(kubectl get networkpolicies -n sock-shop --no-headers 2>/dev/null | wc -l)
    if [ "$hpa_count" -gt 0 ] && [ "$np_count" -gt 0 ]; then
        echo "  Resources reconciled: ${hpa_count} HPAs, ${np_count} NetworkPolicies"
        break
    fi
    sleep 3
    elapsed=$((elapsed + 3))
done

if [ $elapsed -ge $local_timeout ]; then
    echo "  WARNING: controller did not reconcile within ${local_timeout}s — check /tmp/nephio-controller.log"
    cat /tmp/nephio-controller.log | tail -30
fi

# ── 6. Pre-scale gateway services to HPA minReplicas ─────────────────────────
# The HPA controller needs time to detect CPU pressure and spin up new pods.
# By explicitly scaling key deployments now, we guarantee pre-built capacity
# is already running when the attack begins — this is the core advantage of
# Nephio intent-driven management over static native NetworkPolicies.
echo "  Pre-scaling gateway services to HPA minReplicas (3 replicas)..."
for svc in front-end; do
    if kubectl get deployment "$svc" -n sock-shop &>/dev/null; then
        kubectl scale deployment "$svc" --replicas=3 -n sock-shop
        echo "    Scaled $svc → 3 replicas"
    fi
done

echo "  Waiting for front-end rollout to complete..."
kubectl rollout status deployment/front-end -n sock-shop --timeout=120s

echo "  Waiting 30s for HPAs to stabilise and pods to warm up..."
sleep 30
echo "  Nephio-integrated deployment complete."
