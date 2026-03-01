#!/bin/bash
#
# Deploy Cricket App server to Raspberry Pi
#
# Usage: ./scripts/deploy_to_pi.sh [pi-address]
#
# Requirements:
#   - SSH access to Pi (password: Radarcricket12$)
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
echo "[1/5] Checking Pi connectivity..."
if ! ping -c 1 -W 2 "$PI_HOST" > /dev/null 2>&1; then
    echo "ERROR: Cannot reach $PI_HOST"
    exit 1
fi
echo "  Pi is reachable"

# Sync code
echo ""
echo "[2/5] Syncing code to Pi..."

# Create directories first
ssh "$PI_USER@$PI_HOST" "mkdir -p $PI_DIR/server $PI_DIR/db $PI_DIR/engine $PI_DIR/contracts"

# Sync each directory separately to preserve structure
rsync -avz --exclude '__pycache__' --exclude '*.pyc' --exclude '*.db' \
    "$LOCAL_DIR/server/" "$PI_USER@$PI_HOST:$PI_DIR/server/"

rsync -avz --exclude '__pycache__' --exclude '*.pyc' --exclude '*.db' \
    "$LOCAL_DIR/db/" "$PI_USER@$PI_HOST:$PI_DIR/db/"

rsync -avz --exclude '__pycache__' --exclude '*.pyc' \
    "$LOCAL_DIR/engine/" "$PI_USER@$PI_HOST:$PI_DIR/engine/"

rsync -avz --exclude '__pycache__' --exclude '*.pyc' \
    "$LOCAL_DIR/contracts/" "$PI_USER@$PI_HOST:$PI_DIR/contracts/"

# Install dependencies
echo ""
echo "[3/5] Installing Python dependencies..."
ssh "$PI_USER@$PI_HOST" "sudo apt-get install -y python3-websockets"

# Run migrations
echo ""
echo "[4/5] Running database migrations..."
ssh "$PI_USER@$PI_HOST" "cd $PI_DIR && python3 -c '
import sys
sys.path.insert(0, \".\")
from db.migrate import MigrationRunner
from pathlib import Path

runner = MigrationRunner(Path(\"cricket.db\"))
runner.connect()
runner.ensure_migrations_table()
count = runner.run_all_pending()
runner.close()
print(f\"Ran {count} migration(s)\")
'"

# Restart server
echo ""
echo "[5/5] Restarting server..."
ssh "$PI_USER@$PI_HOST" "pkill -f 'python3.*websocket_server' || true; sleep 1; cd $PI_DIR && nohup python3 -m server.websocket_server > server.log 2>&1 &"
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
