#!/bin/bash
#
# Deploy Cricket App server to Raspberry Pi
#
# Usage: ./scripts/deploy_to_pi.sh [pi-address]
#
# Requirements:
#   - SSH access to Pi (key-based auth recommended; see secrets.local.md)
#   - Python 3.9+ on Pi
#   - pip installed on Pi
#

set -e

PI_HOST="${1:-raspberrypi.local}"
PI_USER="bdrysdale"
PI_DIR="/home/bdrysdale/cricket-app"
LOCAL_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Cricket App Pi Deployment ==="
echo "Local: $LOCAL_DIR"
echo "Remote: $PI_USER@$PI_HOST:$PI_DIR"
echo ""

# Check connectivity
echo "[1/6] Checking Pi connectivity..."
if ! ping -c 1 -W 2 "$PI_HOST" > /dev/null 2>&1; then
    echo "ERROR: Cannot reach $PI_HOST"
    exit 1
fi
echo "  Pi is reachable"

# Sync code
echo ""
echo "[2/6] Syncing code to Pi..."

# Create directories first
ssh "$PI_USER@$PI_HOST" "mkdir -p $PI_DIR/server $PI_DIR/db $PI_DIR/engine $PI_DIR/contracts $PI_DIR/radar $PI_DIR/scripts $PI_DIR/tools"

# Sync each directory separately to preserve structure
rsync -avz --exclude '__pycache__' --exclude '*.pyc' --exclude '*.db' \
    "$LOCAL_DIR/server/" "$PI_USER@$PI_HOST:$PI_DIR/server/"

rsync -avz --exclude '__pycache__' --exclude '*.pyc' --exclude '*.db' \
    "$LOCAL_DIR/db/" "$PI_USER@$PI_HOST:$PI_DIR/db/"

rsync -avz --exclude '__pycache__' --exclude '*.pyc' \
    "$LOCAL_DIR/engine/" "$PI_USER@$PI_HOST:$PI_DIR/engine/"

rsync -avz --exclude '__pycache__' --exclude '*.pyc' \
    "$LOCAL_DIR/contracts/" "$PI_USER@$PI_HOST:$PI_DIR/contracts/"

# radar/ is imported by server/handlers.py - the server cannot run without it
rsync -avz --exclude '__pycache__' --exclude '*.pyc' \
    "$LOCAL_DIR/radar/" "$PI_USER@$PI_HOST:$PI_DIR/radar/"

rsync -avz --exclude '__pycache__' --exclude '*.pyc' \
    "$LOCAL_DIR/scripts/" "$PI_USER@$PI_HOST:$PI_DIR/scripts/"

rsync -avz --exclude '__pycache__' --exclude '*.pyc' \
    "$LOCAL_DIR/tools/" "$PI_USER@$PI_HOST:$PI_DIR/tools/"

# Install dependencies
echo ""
echo "[3/6] Installing Python dependencies..."
ssh "$PI_USER@$PI_HOST" "sudo apt-get install -y python3-websockets"

# Install/refresh ALL systemd units shipped in scripts/systemd/
echo ""
echo "[4/6] Installing systemd units..."
ssh "$PI_USER@$PI_HOST" "chmod +x $PI_DIR/scripts/*.sh && \
    sudo cp $PI_DIR/scripts/systemd/*.service /etc/systemd/system/ && \
    sudo systemctl daemon-reload && \
    sudo systemctl enable cricket-autohotspot.service cricket-radar.service \
        cricket-server.service cricket-health.service cricket-ui.service"

# Warn if the radar profile (not in the repo) is missing on the Pi - the radar
# is unconfigured without it and only the single SD-card copy exists.
ssh "$PI_USER@$PI_HOST" "test -f /home/$PI_USER/profile_cricket.cfg" \
    || echo "  WARNING: /home/$PI_USER/profile_cricket.cfg missing on Pi (radar will not configure)"

# Run migrations against the DB the server actually uses (db/cricket.db - see
# db/repository.py DEFAULT_DB_PATH), with a timestamped backup first.
echo ""
echo "[5/6] Backing up and migrating database..."
ssh "$PI_USER@$PI_HOST" "cd $PI_DIR && \
    if [ -f db/cricket.db ]; then cp db/cricket.db db/cricket.db.backup-\$(date +%Y%m%d-%H%M%S); fi && \
    python3 -m db.migrate"

# Restart server via systemd (never pkill/nohup - that races the unit's
# Restart= policy and leaves an unmanaged duplicate serving port 5002)
echo ""
echo "[6/6] Restarting server..."
ssh "$PI_USER@$PI_HOST" "sudo systemctl reset-failed cricket-server.service 2>/dev/null; sudo systemctl restart cricket-server.service"
sleep 2

# Verify
echo ""
echo "=== Deployment Complete ==="
echo "Testing connection..."
python3 -c "
import asyncio
import websockets
import json

async def test():
    async with websockets.connect('ws://$PI_HOST:5002') as ws:
        msg = await asyncio.wait_for(ws.recv(), timeout=5)
        data = json.loads(msg)
        print(f'Server responding: {data[\"type\"]}')
        print(f'Version: {data[\"payload\"][\"server_version\"]}')

asyncio.run(test())
"
