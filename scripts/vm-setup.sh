#!/usr/bin/env bash
# ── One-time setup of the Azure VM ──────────────────────────────────────────
# Swap + k3s install, namespaces + secrets, deploys the app stack.
# (Monitoring/Prometheus/Grafana is disabled — see the note further down.)
# Run from the repo root:  sudo bash scripts/vm-setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if [ "$(id -u)" -ne 0 ]; then
  echo "This script must run as root (k3s install): re-running with sudo"
  exec sudo bash "$0" "$@"
fi

# ── 1 GB swap ───────────────────────────────────────────────────────────────
# The VM has 1 GB RAM; without swap a single memory spike OOM-kills pods.
if [ -z "$(swapon --show --noheadings 2>/dev/null || true)" ]; then
  echo "==> Enabling 1 GB swap"
  fallocate -l 1G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=1024 status=none
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
else
  echo "==> Swap already enabled"
fi

KUBELET_ARGS="--kubelet-arg=allowed-unsafe-sysctls=net.ipv4.ping_group_range --kubelet-arg=fail-swap-on=false"

if command -v k3s >/dev/null 2>&1; then
  echo "==> k3s already installed — ensuring kubelet args (fail-swap-on=false, ping sysctl)"
  UNIT=/etc/systemd/system/k3s.service
  if [ -f "$UNIT" ] && ! grep -q 'fail-swap-on' "$UNIT"; then
    sed -i "s|k3s server|k3s server $KUBELET_ARGS|" "$UNIT"
    systemctl daemon-reload
    systemctl restart k3s
    sleep 5
  fi
else
  echo "==> Installing k3s (traefik/servicelb disabled — Cloudflare Tunnel is the ingress)"
  # fail-swap-on=false: kubelet tolerates the swapfile above.
  # allowed-unsafe-sysctls: lets cloudflared use net.ipv4.ping_group_range (ICMP
  # edge health checks). If rejected, drop that arg and the sysctls block in
  # k8s/cloudflared.yaml — the tunnel works either way.
  curl -sfL https://get.k3s.io | \
    INSTALL_K3S_EXEC="server --disable traefik --disable servicelb $KUBELET_ARGS" \
    sh -
fi

echo "==> Waiting for the node to be ready"
kubectl wait --for=condition=Ready node --all --timeout=120s

echo "==> Creating namespaces (myreviewer, monitoring)"
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/monitoring/namespace.yaml

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

# Grafana secret disabled along with the monitoring stack (see below). Re-enable
# with the monitoring deploy if you turn monitoring back on.
# if kubectl -n monitoring get secret grafana-admin >/dev/null 2>&1; then
#   echo "    grafana-admin already exists — skipping"
# else
#   read -r -p "Grafana admin user [admin]: " GF_USER
#   GF_USER="${GF_USER:-admin}"
#   read -r -s -p "Grafana admin password: " GF_PASS
#   echo
#   [ -n "$GF_PASS" ] || { echo "empty password"; exit 1; }
#   kubectl -n monitoring create secret generic grafana-admin \
#     --from-literal=admin-user="$GF_USER" \
#     --from-literal=admin-password="$GF_PASS"
# fi

echo "==> Deploying app manifests"
kubectl apply -k k8s/

# Monitoring stack disabled: this VM is memory-constrained, and Prometheus +
# Grafana push k3s' datastore (kine) into I/O starvation, freezing the API.
# Re-enable by uncommenting the line below.
echo "==> Monitoring stack disabled (skipping Prometheus + Grafana + node-exporter)"
# kubectl apply -k k8s/monitoring/

echo "==> Waiting for the in-cluster cloudflared connector"
CLOUDFLARED_READY=0
if kubectl -n myreviewer rollout status deploy/cloudflared --timeout=180s; then
  CLOUDFLARED_READY=1
else
  echo "!! in-cluster cloudflared not ready — check: kubectl -n myreviewer logs deploy/cloudflared"
fi

echo "==> Host cloudflared service (must yield to the in-cluster connector)"
HOST_CLOUDFLARED="$(systemctl list-units --type=service --all --no-legend 2>/dev/null \
  | awk '{print $1}' | grep -E '^cloudflared.*\.service$' | head -n1 || true)"
if [ -n "$HOST_CLOUDFLARED" ]; then
  if [ "$CLOUDFLARED_READY" -eq 1 ]; then
    read -r -p "Host service '$HOST_CLOUDFLARED' detected. Two connectors on one tunnel cause route flapping. Disable it? [Y/n] " ANS
    case "${ANS:-Y}" in
      [Nn]*)
        echo "    kept — then remove the in-cluster connector instead:"
        echo "    kubectl -n myreviewer delete deploy cloudflared; kubectl -n myreviewer delete svc cloudflared-metrics"
        ;;
      *)
        systemctl disable --now "$HOST_CLOUDFLARED"
        echo "    $HOST_CLOUDFLARED disabled (in-cluster connector takes over)"
        ;;
    esac
  else
    echo "    NOT disabling '$HOST_CLOUDFLARED' while the in-cluster connector is down (downtime)."
  fi
else
  echo "    no host cloudflared service found"
fi

echo "==> Status"
kubectl -n myreviewer get pods,svc,pvc -o wide
echo
kubectl -n monitoring get pods,svc,pvc -o wide
echo
echo "Done. Next (if not done yet):"
echo "  1. Cloudflare Zero Trust -> myreviewer tunnel -> Published application routes:"
echo "       myreviewer.tech          ->  http://myreviewer-front.myreviewer.svc.cluster.local:80"
echo "       grafana.myreviewer.tech   ->  http://grafana.monitoring.svc.cluster.local:80"
echo "  2. scripts/runner-setup.sh <repo-url> <runner-token>   (CI/CD self-hosted runner)"
echo "  3. Open https://grafana.myreviewer.tech (user/password from this script)"
