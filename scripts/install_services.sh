#!/bin/bash
#
# Install CricketRadar systemd services
#
# Run this script on the Raspberry Pi to set up auto-start services.
# Requires sudo access.
#
set -e  # Exit on error

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SYSTEMD_DIR="/etc/systemd/system"

echo "=== CricketRadar Service Installation ==="
echo ""

# Check we're running as root or with sudo
if [[ $EUID -ne 0 ]]; then
   echo "This script must be run with sudo"
   echo "Usage: sudo $0"
   exit 1
fi

# Check service files exist
if [[ ! -f "$SCRIPT_DIR/systemd/cricket-radar.service" ]]; then
    echo "ERROR: cricket-radar.service not found in $SCRIPT_DIR/systemd/"
    exit 1
fi

if [[ ! -f "$SCRIPT_DIR/systemd/cricket-server.service" ]]; then
    echo "ERROR: cricket-server.service not found in $SCRIPT_DIR/systemd/"
    exit 1
fi

if [[ ! -f "$SCRIPT_DIR/systemd/cricket-health.service" ]]; then
    echo "WARNING: cricket-health.service not found, skipping health monitor"
    INSTALL_HEALTH=0
else
    INSTALL_HEALTH=1
fi

echo "1. Stopping existing services (if any)..."
systemctl stop cricket-health.service 2>/dev/null || true
systemctl stop cricket-server.service 2>/dev/null || true
systemctl stop cricket-radar.service 2>/dev/null || true

echo "2. Copying service files to $SYSTEMD_DIR..."
cp "$SCRIPT_DIR/systemd/cricket-radar.service" "$SYSTEMD_DIR/"
cp "$SCRIPT_DIR/systemd/cricket-server.service" "$SYSTEMD_DIR/"
if [[ $INSTALL_HEALTH -eq 1 ]]; then
    cp "$SCRIPT_DIR/systemd/cricket-health.service" "$SYSTEMD_DIR/"
fi

echo "3. Setting permissions..."
chmod 644 "$SYSTEMD_DIR/cricket-radar.service"
chmod 644 "$SYSTEMD_DIR/cricket-server.service"
if [[ $INSTALL_HEALTH -eq 1 ]]; then
    chmod 644 "$SYSTEMD_DIR/cricket-health.service"
fi

echo "4. Reloading systemd daemon..."
systemctl daemon-reload

echo "5. Enabling services to start on boot..."
systemctl enable cricket-radar.service
systemctl enable cricket-server.service
if [[ $INSTALL_HEALTH -eq 1 ]]; then
    systemctl enable cricket-health.service
fi

echo "6. Starting services now..."
systemctl start cricket-radar.service
# Wait for radar config to complete
echo "   Waiting for radar configuration..."
systemctl start cricket-server.service
if [[ $INSTALL_HEALTH -eq 1 ]]; then
    echo "   Starting health monitor..."
    systemctl start cricket-health.service
fi

echo ""
echo "=== Installation Complete ==="
echo ""
echo "Service status:"
systemctl status cricket-radar.service --no-pager -l 2>&1 | head -15 || true
echo ""
systemctl status cricket-server.service --no-pager -l 2>&1 | head -15 || true
if [[ $INSTALL_HEALTH -eq 1 ]]; then
    echo ""
    systemctl status cricket-health.service --no-pager -l 2>&1 | head -15 || true
fi
echo ""
echo "Useful commands:"
echo "  View radar logs:    journalctl -u cricket-radar -f"
echo "  View server logs:   journalctl -u cricket-server -f"
echo "  View health logs:   journalctl -u cricket-health -f"
echo "  View all logs:      journalctl -u 'cricket-*' -f"
echo "  Restart services:   sudo systemctl restart cricket-radar cricket-server cricket-health"
echo "  Disable auto-start: sudo systemctl disable cricket-radar cricket-server cricket-health"
echo ""
