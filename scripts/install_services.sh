#!/usr/bin/env bash
#
# Install every CricketRadar systemd unit on the Raspberry Pi.
#
# Run ON the Pi, with sudo, from a checkout at /home/bdrysdale/cricket-app.
# deploy_to_pi.sh does the same thing over SSH; this is the on-device path
# for a fresh card. It installs ALL units shipped in scripts/systemd/ - the
# previous version installed three of five, so a Pi set up this way had
# nothing serving the app and no hotspot.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
UNIT_DIR="$SCRIPT_DIR/systemd"
SYSTEMD_DIR="/etc/systemd/system"

echo "=== CricketRadar Service Installation ==="
echo ""

if [[ $EUID -ne 0 ]]; then
   echo "This script must be run with sudo"
   echo "Usage: sudo $0"
   exit 1
fi

shopt -s nullglob
units=("$UNIT_DIR"/*.service)
if [[ ${#units[@]} -eq 0 ]]; then
    echo "ERROR: no *.service files in $UNIT_DIR"
    exit 1
fi

# The radar profile the radar unit sends at boot lives in the repo now
if [[ ! -f "$SCRIPT_DIR/../config/profile_cricket.cfg" ]]; then
    echo "ERROR: config/profile_cricket.cfg is missing - the radar cannot be configured"
    exit 1
fi

# The server cannot start without these; say so before enabling anything
if ! python3 -c 'import websockets, serial' 2>/dev/null; then
    echo "ERROR: python3-websockets and/or python3-serial are missing."
    echo "       sudo apt-get install python3-websockets python3-serial"
    exit 1
fi

echo "1. Stopping existing services (if any)..."
for unit in "${units[@]}"; do
    systemctl stop "$(basename "$unit")" 2>/dev/null || true
done

echo "2. Installing units to $SYSTEMD_DIR..."
for unit in "${units[@]}"; do
    install -m 644 "$unit" "$SYSTEMD_DIR/"
    echo "   $(basename "$unit")"
done
if [[ -f "$UNIT_DIR/journald-cricket.conf" ]]; then
    mkdir -p /etc/systemd/journald.conf.d
    install -m 644 "$UNIT_DIR/journald-cricket.conf" /etc/systemd/journald.conf.d/cricket.conf
    echo "   journald cap (journald.conf.d/cricket.conf)"
fi

echo "3. Reloading systemd..."
systemctl daemon-reload
# Surface any directive systemd would silently ignore
systemd-analyze verify "$SYSTEMD_DIR"/cricket-*.service 2>&1 | grep -v '^$' || true

echo "4. Enabling services at boot..."
for unit in "${units[@]}"; do
    systemctl enable "$(basename "$unit")"
done

echo "5. Starting services now..."
# autohotspot is a boot-time decision (it starts the AP if no known WiFi
# appears); enabling it is enough - starting it now on a LAN would do nothing.
systemctl start cricket-radar.service
echo "   Waiting for radar configuration..."
systemctl start cricket-server.service cricket-ui.service cricket-health.service

echo ""
echo "=== Installation Complete ==="
echo ""
for unit in "${units[@]}"; do
    name="$(basename "$unit")"
    printf '%-32s %s\n' "$name" "$(systemctl is-active "$name" || true)"
done
echo ""
echo "Useful commands:"
echo "  View all logs:      journalctl -u 'cricket-*' -f"
echo "  Restart server:     sudo systemctl restart cricket-server"
echo "  Service status:     systemctl status 'cricket-*'"
