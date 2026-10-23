#!/usr/bin/env bash
# Restarts k3s when its API stops responding (the 1 GB VM wedges under memory
# pressure, which takes the site down with Cloudflare error 1033).
#
# Install (one time, as root):
#   sudo install -m755 scripts/k3s-watchdog.sh /usr/local/bin/k3s-watchdog.sh
#   sudo cp scripts/k3s-watchdog.service scripts/k3s-watchdog.timer /etc/systemd/system/
#   sudo systemctl daemon-reload
#   sudo systemctl enable --now k3s-watchdog.timer
set -euo pipefail

export KUBECONFIG="${KUBECONFIG:-/home/myreviewer/.kube/config}"
STATE_FILE="${STATE_FILE:-/run/k3s-watchdog.failures}"
THRESHOLD="${THRESHOLD:-3}"

if timeout 15 kubectl get --raw /readyz >/dev/null 2>&1; then
  rm -f "$STATE_FILE"
  exit 0
fi

n=1
if [ -f "$STATE_FILE" ]; then
  n=$(( $(cat "$STATE_FILE") + 1 ))
fi
echo "$n" > "$STATE_FILE"
echo "$(date -Is) k3s API not ready ($n/$THRESHOLD)"

if [ "$n" -ge "$THRESHOLD" ]; then
  echo "$(date -Is) restarting k3s"
  systemctl restart k3s
  rm -f "$STATE_FILE"
fi
