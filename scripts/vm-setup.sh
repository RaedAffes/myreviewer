#!/usr/bin/env bash
# ── One-time setup of the Azure VM ──────────────────────────────────────────
# Installs k3s, creates the namespace + secrets, deploys the manifests.
# Run from the repo root:  sudo bash scripts/vm-setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if [ "$(id -u)" -ne 0 ]; then
  echo "This script must run as root (k3s install): re-running with sudo"
  exec sudo bash "$0" "$@"
fi

echo "==> Installing k3s (traefik/servicelb disabled — Cloudflare Tunnel is the ingress)"
# --kubelet-arg lets the cloudflared pod use net.ipv4.ping_group_range (ICMP
# edge health checks). If your k3s install rejects it, drop that arg and the
# sysctls block in k8s/cloudflared.yaml — the tunnel works either way.
curl -sfL https://get.k3s.io | \
  INSTALL_K3S_EXEC="server --disable traefik --disable servicelb --kubelet-arg=allowed-unsafe-sysctls=net.ipv4.ping_group_range" \
  sh -

echo "==> Waiting for the node to be ready"
kubectl wait --for=condition=Ready node --all --timeout=120s

echo "==> Creating namespace"
kubectl apply -f k8s/namespace.yaml

echo "==> Creating secrets"
if kubectl -n myreviewer get secret tunnel-token >/dev/null 2>&1; then
  echo "    tunnel-token already exists — skipping"
else
  read -r -p "Cloudflare tunnel token (Zero Trust -> Networks -> Tunnels -> myreviewer): " TUNNEL_TOKEN
  [ -n "$TUNNEL_TOKEN" ] || { echo "empty token"; exit 1; }
  kubectl -n myreviewer create secret generic tunnel-token \
    --from-literal=token="$TUNNEL_TOKEN"
fi

if kubectl -n myreviewer get secret myreviewer-env >/dev/null 2>&1; then
  echo "    myreviewer-env already exists — skipping"
else
  if [ ! -f back/.env ]; then
    echo "!! back/.env not found. Create the secret before the pods can run:"
    echo "   kubectl -n myreviewer create secret generic myreviewer-env --from-env-file=back/.env"
    exit 1
  fi
  # kubectl's env-file parser is strict: keep only KEY=VALUE lines.
  FILTERED_ENV="$(mktemp)"
  grep -E '^[A-Za-z_][A-Za-z0-9_]*=' back/.env > "$FILTERED_ENV"
  kubectl -n myreviewer create secret generic myreviewer-env \
    --from-env-file="$FILTERED_ENV"
  rm -f "$FILTERED_ENV"
fi

echo "==> Deploying manifests"
kubectl apply -f k8s/namespace.yaml
kubectl apply -k k8s/

echo "==> Status"
kubectl -n myreviewer get pods,svc,pvc -o wide
echo
echo "Done. Next (if not done yet):"
echo "  1. Cloudflare Zero Trust -> Networks -> Tunnels -> myreviewer -> Public Hostname:"
echo "       myreviewer.tech  ->  http://myreviewer-front.myreviewer.svc.cluster.local:80"
echo "  2. scripts/runner-setup.sh <repo-url> <runner-token>   (CI/CD self-hosted runner)"
