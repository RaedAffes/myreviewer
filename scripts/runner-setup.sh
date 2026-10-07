#!/usr/bin/env bash
# ── GitHub Actions self-hosted runner (the VM side of CI/CD) ────────────────
# The runner connects OUT to GitHub (no public IP needed) and executes the
# deploy job locally, where kubectl talks to k3s on 127.0.0.1:6443.
#
# Usage:
#   1. GitHub -> your repo -> Settings -> Actions -> Runners -> New self-hosted runner
#      copy the --url and --token values
#   2. bash scripts/runner-setup.sh https://github.com/OWNER/myreviewer TOKEN
set -euo pipefail

REPO_URL="${1:-}"
TOKEN="${2:-}"
if [ -z "$REPO_URL" ] || [ -z "$TOKEN" ]; then
  echo "Usage: bash scripts/runner-setup.sh <repo-url> <runner-token>"
  exit 1
fi

RUNNER_DIR="/opt/actions-runner"
case "$(uname -m)" in
  x86_64)  RUNNER_ARCH="x64" ;;
  aarch64) RUNNER_ARCH="arm64" ;;
  *) echo "unsupported architecture: $(uname -m)"; exit 1 ;;
esac

echo "==> Downloading the latest runner"
VER=$(curl -sS https://api.github.com/repos/actions/runner/releases/latest \
  | grep -m1 '"tag_name"' | sed -E 's/.*"v([^"]+)".*/\1/')
[ -n "$VER" ] || { echo "could not resolve runner version"; exit 1; }
echo "    version $VER ($RUNNER_ARCH)"

sudo mkdir -p "$RUNNER_DIR"
cd "$RUNNER_DIR"
sudo chown -R "$(id -u):$(id -g)" "$RUNNER_DIR"

TARBALL="actions-runner-linux-${RUNNER_ARCH}-${VER}.tar.gz"
curl -sSfL -o "$TARBALL" \
  "https://github.com/actions/runner/releases/download/v${VER}/${TARBALL}"
tar xzf "$TARBALL"
rm -f "$TARBALL"

echo "==> Registering the runner"
./config.sh --unattended --replace \
  --url "$REPO_URL" --token "$TOKEN" \
  --name "azure-vm-k3s"

echo "==> Granting the runner user access to k3s"
sudo mkdir -p "$HOME/.kube"
sudo cp /etc/rancher/k3s/k3s.yaml "$HOME/.kube/config"
sudo chown "$(id -u):$(id -g)" "$HOME/.kube/config"
sudo chmod 600 "$HOME/.kube/config"

echo "==> Installing the runner as a systemd service"
sudo ./svc.sh install "$(id -un)"
sudo ./svc.sh start

echo "==> Done"
sudo ./svc.sh status || true
