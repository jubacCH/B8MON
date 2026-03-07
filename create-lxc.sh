#!/usr/bin/env bash
# =============================================================================
# Nodeglow LXC Setup Script for Proxmox
# Run on the Proxmox host: bash create-lxc.sh
# =============================================================================
set -e

# ── Configuration ────────────────────────────────────────────────────────────
CTID=200                        # Container ID (change if taken)
HOSTNAME="nodeglow"
STORAGE="local-lvm"
DISK_SIZE="8"                   # GB
RAM=1024                        # MB
CORES=2
IP="10.10.30.52/24"
GATEWAY="10.10.30.1"
DNS="10.10.30.1"
BRIDGE="vmbr0"
TEMPLATE="debian-12-standard_12.7-1_amd64.tar.zst"
TEMPLATE_STORAGE="local"
PORT=8000
# ─────────────────────────────────────────────────────────────────────────────

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[✗]${NC} $1"; exit 1; }

[[ $EUID -ne 0 ]] && error "Must be run as root on the Proxmox host"

if pct status $CTID &>/dev/null; then
  error "Container ID $CTID already in use. Change CTID in this script."
fi

# ── Download template ───────────────────────────────────────────────────────
info "Checking Debian 12 template..."
TEMPLATE_PATH="/var/lib/vz/template/cache/$TEMPLATE"
if [[ ! -f "$TEMPLATE_PATH" ]]; then
  warn "Template not found – downloading..."
  pveam update
  pveam download $TEMPLATE_STORAGE $TEMPLATE || \
    error "Template download failed. Check: pveam available | grep debian-12"
else
  info "Template already present."
fi

# ── Create LXC ──────────────────────────────────────────────────────────────
info "Creating LXC container (ID: $CTID)..."
pct create $CTID \
  ${TEMPLATE_STORAGE}:vztmpl/$TEMPLATE \
  --hostname $HOSTNAME \
  --storage $STORAGE \
  --rootfs ${STORAGE}:${DISK_SIZE} \
  --memory $RAM \
  --cores $CORES \
  --net0 name=eth0,bridge=$BRIDGE,ip=$IP,gw=$GATEWAY \
  --nameserver $DNS \
  --features nesting=1 \
  --unprivileged 1 \
  --start 1

info "Container started. Waiting for boot..."
sleep 5

# ── System update + Docker ──────────────────────────────────────────────────
info "Installing system updates and Docker..."
pct exec $CTID -- bash -c "
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq curl git ca-certificates gnupg 2>/dev/null

  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo 'deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian bookworm stable' \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin 2>/dev/null
  systemctl enable --now docker
"
info "Docker installed."

# ── Clone and start Nodeglow ────────────────────────────────────────────────
info "Cloning Nodeglow repository..."
pct exec $CTID -- bash -c "
  git clone https://github.com/jubacCH/Vigil.git /opt/nodeglow
  cd /opt/nodeglow
  docker compose up -d
"

# ── Autostart on Proxmox reboot ─────────────────────────────────────────────
info "Setting LXC to autostart..."
pct set $CTID --onboot 1

# ── Done ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}══════════════════════════════════════════${NC}"
echo -e "${GREEN}  Nodeglow is running!${NC}"
echo -e "${GREEN}══════════════════════════════════════════${NC}"
echo ""
echo -e "  Dashboard:   ${YELLOW}http://${IP%/*}:${PORT}${NC}"
echo -e "  LXC shell:   ${YELLOW}pct enter $CTID${NC}"
echo -e "  Logs:        ${YELLOW}pct exec $CTID -- docker compose -f /opt/nodeglow/docker-compose.yml logs -f${NC}"
echo -e "  Update:      ${YELLOW}pct exec $CTID -- bash -c 'cd /opt/nodeglow && git pull && docker compose up -d --build'${NC}"
echo ""
