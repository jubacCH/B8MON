#!/usr/bin/env bash
# =============================================================================
# Vigil LXC Setup Script für Proxmox
# Ausführen auf dem Proxmox-Host: bash create-lxc.sh
# =============================================================================
set -e

# ── Konfiguration ─────────────────────────────────────────────────────────────
CTID=200                        # Container-ID (ändern falls belegt)
HOSTNAME="vigil"
STORAGE="local-lvm"
DISK_SIZE="8"                   # GB
RAM=1024                        # MB
CORES=2
IP="10.10.30.52/24"
GATEWAY="10.10.30.1"
DNS="10.10.30.1"
BRIDGE="vmbr0"
TEMPLATE="debian-12-standard_12.7-1_amd64.tar.zst"
TEMPLATE_STORAGE="local"        # Wo Templates gespeichert werden
PORT=8000
# ─────────────────────────────────────────────────────────────────────────────

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[✗]${NC} $1"; exit 1; }

# Root-Check
[[ $EUID -ne 0 ]] && error "Bitte als root ausführen (auf dem Proxmox-Host)"

# CTID prüfen
if pct status $CTID &>/dev/null; then
  error "Container-ID $CTID ist bereits vergeben. Bitte CTID in diesem Script ändern."
fi

# ── Template herunterladen ────────────────────────────────────────────────────
info "Prüfe Debian 12 Template..."
TEMPLATE_PATH="/var/lib/vz/template/cache/$TEMPLATE"
if [[ ! -f "$TEMPLATE_PATH" ]]; then
  warn "Template nicht gefunden – lade herunter..."
  pveam update
  pveam download $TEMPLATE_STORAGE $TEMPLATE || \
    error "Template-Download fehlgeschlagen. Prüfe: pveam available | grep debian-12"
else
  info "Template bereits vorhanden."
fi

# ── LXC erstellen ─────────────────────────────────────────────────────────────
info "Erstelle LXC Container (ID: $CTID)..."
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

info "Container gestartet. Warte auf Boot..."
sleep 5

# ── System-Update + Docker ────────────────────────────────────────────────────
info "System-Update und Docker-Installation..."
pct exec $CTID -- bash -c "
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq curl git ca-certificates gnupg 2>/dev/null

  # Docker offiziell installieren
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo 'deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian bookworm stable' \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin 2>/dev/null
  systemctl enable --now docker
"
info "Docker installiert."

# ── Vigil clonen und starten ──────────────────────────────────────────────────
info "Klone Vigil Repository..."
pct exec $CTID -- bash -c "
  git clone https://github.com/jubacCH/B8MON.git /opt/vigil
  cd /opt/vigil

  # Produktions-Konfiguration: live-mount und --reload entfernen
  cat > docker-compose.override.yml << 'EOF'
services:
  monitor:
    volumes:
      - ./data:/data
    command: [\"uvicorn\", \"main:app\", \"--host\", \"0.0.0.0\", \"--port\", \"8000\"]
EOF

  docker compose up -d
"

# ── Auto-Start beim Proxmox-Reboot ───────────────────────────────────────────
info "Setze LXC auf Autostart..."
pct set $CTID --onboot 1

# ── Fertig ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}══════════════════════════════════════════${NC}"
echo -e "${GREEN}  Vigil läuft!${NC}"
echo -e "${GREEN}══════════════════════════════════════════${NC}"
echo ""
echo -e "  Dashboard:   ${YELLOW}http://${IP%/*}:${PORT}${NC}"
echo -e "  LXC-Shell:   ${YELLOW}pct enter $CTID${NC}"
echo -e "  Logs:        ${YELLOW}pct exec $CTID -- docker compose -f /opt/vigil/docker-compose.yml logs -f${NC}"
echo -e "  Update:      ${YELLOW}pct exec $CTID -- bash -c 'cd /opt/vigil && git pull && docker compose up -d --build'${NC}"
echo ""
