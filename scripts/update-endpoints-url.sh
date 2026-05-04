#!/usr/bin/env bash
#
# Update discovered-endpoints.json with current NodePort URL
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENDPOINTS_FILE="$PROJECT_ROOT/attacks/discovered-endpoints.json"

# Get minikube IP and NodePort
MINIKUBE_IP=$(minikube ip)
NODE_PORT=$(kubectl get svc -n sock-shop front-end -o jsonpath='{.spec.ports[0].nodePort}')

if [ -z "$NODE_PORT" ]; then
    echo "Error: Could not get NodePort for front-end service"
    exit 1
fi

NEW_BASE_URL="http://${MINIKUBE_IP}:${NODE_PORT}"

echo "Updating discovered-endpoints.json..."
echo "  Old base_url: $(jq -r '.base_url' "$ENDPOINTS_FILE")"
echo "  New base_url: $NEW_BASE_URL"

# Update base_url in JSON file
jq --arg url "$NEW_BASE_URL" '.base_url = $url' "$ENDPOINTS_FILE" > "${ENDPOINTS_FILE}.tmp"
mv "${ENDPOINTS_FILE}.tmp" "$ENDPOINTS_FILE"

# Update all discovered_urls to use new base
jq --arg url "$NEW_BASE_URL" '
  .discovered_urls = (.discovered_urls | map(
    if startswith("http://") then
      ($url + (. | sub("^http://[^/]+"; "")))
    else
      ($url + .)
    end
  ))
' "$ENDPOINTS_FILE" > "${ENDPOINTS_FILE}.tmp"
mv "${ENDPOINTS_FILE}.tmp" "$ENDPOINTS_FILE"

# Update api_endpoints
jq --arg url "$NEW_BASE_URL" '
  .api_endpoints = (.api_endpoints | map(
    if startswith("http://") then
      ($url + (. | sub("^http://[^/]+"; "")))
    else
      ($url + .)
    end
  ))
' "$ENDPOINTS_FILE" > "${ENDPOINTS_FILE}.tmp"
mv "${ENDPOINTS_FILE}.tmp" "$ENDPOINTS_FILE"

# Update endpoint_profiles keys
jq --arg url "$NEW_BASE_URL" '
  .endpoint_profiles = (
    .endpoint_profiles | to_entries | map(
      {
        key: (if .key | startswith("http://") then
                ($url + (.key | sub("^http://[^/]+"; "")))
              else
                ($url + .key)
              end),
        value: (.value | .url = (if .value.url | startswith("http://") then
                                    ($url + (.value.url | sub("^http://[^/]+"; "")))
                                  else
                                    ($url + .value.url)
                                  end))
      }
    ) | from_entries
  )
' "$ENDPOINTS_FILE" > "${ENDPOINTS_FILE}.tmp"
mv "${ENDPOINTS_FILE}.tmp" "$ENDPOINTS_FILE"

echo "✓ Updated discovered-endpoints.json with NodePort URL"
echo "  Target: $NEW_BASE_URL"
