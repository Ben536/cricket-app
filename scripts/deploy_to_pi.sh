#!/usr/bin/env bash
#
# Deploy CricketRadar to the Raspberry Pi.
#
# Usage: ./scripts/deploy_to_pi.sh [pi-address]
#
# Requirements:
#   - SSH key access to the Pi as $PI_USER (see vault/learnings/Pi Deployment and Ops.md)
#   - Python 3.9+ with python3-websockets and python3-serial on the Pi
#     (this script installs them via apt when the Pi has internet; when it
#     does not, it verifies they are already present and stops if not)
#
# Order of operations matters. Every check that can fail runs BEFORE a single
# file lands on the Pi, so a failed deploy leaves the old server serving the
# old code rather than new code on disk waiting to start against an
# unmigrated database on the next restart or reboot:
#   1. reachability  2. python deps on the Pi  3. build the frontend locally
#   4. sync code + dist  5. systemd units  6. backup + migrate DB
#   7. restart  8. verify
#
set -euo pipefail

PI_HOST="${1:-cricketradar.local}"
PI_USER="bdrysdale"
PI_DIR="/home/bdrysdale/cricket-app"
LOCAL_DIR="$(cd "$(dirname "$0")/.." && pwd)"

RSYNC_EXCLUDES=(--exclude '__pycache__' --exclude '*.pyc' --exclude '.pytest_cache')
# Never ship a database or its WAL/shm sidecars: a local WAL copied next to a
# DIFFERENT cricket.db replays foreign frames into it on open. Excluded files
# are also protected from --delete on the Pi side.
DB_EXCLUDES=(--exclude '*.db' --exclude '*.db-wal' --exclude '*.db-shm' --exclude '*.db.backup-*')

remote() { ssh "$PI_USER@$PI_HOST" "$@"; }

echo "=== CricketRadar Pi Deployment ==="
echo "Local:  $LOCAL_DIR"
echo "Remote: $PI_USER@$PI_HOST:$PI_DIR"
echo ""

# --- 1. reachability ----------------------------------------------------------
echo "[1/8] Checking Pi connectivity..."
if ! ping -c 1 -W 2 "$PI_HOST" > /dev/null 2>&1; then
    echo "ERROR: Cannot reach $PI_HOST"
    exit 1
fi
remote "true" || { echo "ERROR: SSH to $PI_USER@$PI_HOST failed"; exit 1; }
echo "  Pi is reachable"

# --- 2. python deps on the Pi -------------------------------------------------
echo ""
echo "[2/8] Python dependencies on the Pi..."
# apt needs internet, which the Pi does not have at the nets. Try, but treat
# failure as "offline" and fall through to the check that actually matters.
if ! remote "sudo apt-get install -y python3-websockets python3-serial" > /dev/null 2>&1; then
    echo "  apt-get failed (Pi offline?) - checking what is already installed"
fi
if ! remote "python3 -c 'import websockets, serial'" 2>/dev/null; then
    echo "ERROR: python3-websockets and/or python3-serial are not installed on the Pi."
    echo "       The server cannot start without them. Connect the Pi to the internet and re-run,"
    echo "       or: sudo apt-get install python3-websockets python3-serial"
    echo "       Nothing has been changed on the Pi."
    exit 1
fi
echo "  websockets + pyserial present: $(remote "python3 -c 'import websockets, serial; print(websockets.__version__, serial.__version__)'")"

# --- 3. build the frontend locally --------------------------------------------
echo ""
echo "[3/8] Building the frontend..."
if command -v npm >/dev/null 2>&1; then
    if ! (cd "$LOCAL_DIR" && npm run build); then
        echo "ERROR: npm run build failed. Nothing has been changed on the Pi."
        exit 1
    fi
elif [ -d "$LOCAL_DIR/dist" ]; then
    echo "  npm not available - shipping the existing dist/ ($(stat -f '%Sm' "$LOCAL_DIR/dist" 2>/dev/null || stat -c '%y' "$LOCAL_DIR/dist"))"
else
    echo "ERROR: no npm and no dist/ - the Pi would have no UI to serve."
    exit 1
fi

# --- 4. sync code + dist ------------------------------------------------------
echo ""
echo "[4/8] Syncing code to Pi..."

# `recordings` is gitignored so it never arrives via rsync, but cricket-server's
# ReadWritePaths names it - create it so the recorder has somewhere to write.
remote "mkdir -p $PI_DIR/server $PI_DIR/db $PI_DIR/engine $PI_DIR/contracts \
    $PI_DIR/radar $PI_DIR/scripts $PI_DIR/tools $PI_DIR/config $PI_DIR/recordings"

# --delete so files removed from the repo do not linger on the Pi (the
# excludes above are protected from deletion).
for dir in server db engine contracts radar scripts tools config; do
    rsync -az --delete "${RSYNC_EXCLUDES[@]}" "${DB_EXCLUDES[@]}" \
        "$LOCAL_DIR/$dir/" "$PI_USER@$PI_HOST:$PI_DIR/$dir/"
done
rsync -az --delete "$LOCAL_DIR/dist/" "$PI_USER@$PI_HOST:$PI_DIR/dist/"
echo "  Code and frontend synced (server db engine contracts radar scripts tools config dist)"

# The radar profile the unit sends at boot is now the repo copy
# (config/profile_cricket.cfg). A hand-edited copy in the home directory used
# to be the only one that existed; if it still differs, say so loudly rather
# than silently reverting a tuning change.
if remote "test -f /home/$PI_USER/profile_cricket.cfg"; then
    if ! remote "diff -q /home/$PI_USER/profile_cricket.cfg $PI_DIR/config/profile_cricket.cfg" >/dev/null 2>&1; then
        echo ""
        echo "  WARNING: /home/$PI_USER/profile_cricket.cfg on the Pi DIFFERS from config/profile_cricket.cfg."
        echo "  The radar service now uses the repo copy. Diff (Pi vs repo):"
        remote "diff /home/$PI_USER/profile_cricket.cfg $PI_DIR/config/profile_cricket.cfg" || true
        echo "  If the Pi copy is the tuned one, copy it into config/ and redeploy."
        echo ""
    fi
fi

# --- 5. systemd units ---------------------------------------------------------
echo ""
echo "[5/8] Installing systemd units..."
remote "chmod +x $PI_DIR/scripts/*.sh && \
    sudo cp $PI_DIR/scripts/systemd/*.service /etc/systemd/system/ && \
    sudo mkdir -p /etc/systemd/journald.conf.d && \
    sudo cp $PI_DIR/scripts/systemd/journald-cricket.conf /etc/systemd/journald.conf.d/cricket.conf && \
    sudo systemctl daemon-reload && \
    (sudo systemctl restart systemd-journald || echo '  (journald restart failed - cap applies after reboot)') && \
    sudo systemctl enable cricket-autohotspot.service cricket-radar.service \
        cricket-server.service cricket-health.service cricket-ui.service"
# Every directive systemd would silently ignore shows up here
remote "systemd-analyze verify /etc/systemd/system/cricket-*.service 2>&1 | grep -v '^$' || true"
echo "  Units installed and enabled"

# --- 6. backup + migrate ------------------------------------------------------
echo ""
echo "[6/8] Backing up and migrating database..."
# sqlite's online backup API produces a CONSISTENT copy even with a WAL and an
# open connection. `cp` of the .db alone did not: it silently omitted every
# transaction still in the -wal file, so the documented rollback procedure
# restored a stale database.
remote "cd $PI_DIR && if [ -f db/cricket.db ]; then python3 - <<'PY'
import sqlite3, datetime
stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
dst_path = f'db/cricket.db.backup-{stamp}'
src = sqlite3.connect('db/cricket.db'); dst = sqlite3.connect(dst_path)
src.backup(dst); dst.close(); src.close()
print(f'  backup: {dst_path}')
PY
fi && python3 -m db.migrate"

# --- 7. restart ---------------------------------------------------------------
echo ""
echo "[7/8] Restarting services..."
# Restart via systemd (never pkill/nohup - that races the unit's Restart=
# policy and leaves an unmanaged duplicate serving port 5002)
remote "sudo systemctl reset-failed cricket-server.service cricket-ui.service 2>/dev/null; \
    sudo systemctl restart cricket-server.service cricket-ui.service"
sleep 2

# --- 8. verify ----------------------------------------------------------------
echo ""
echo "[8/8] Verifying..."
remote "systemctl is-active cricket-server.service cricket-ui.service cricket-health.service" || {
    echo "ERROR: a service is not active. On the Pi: journalctl -u cricket-server -n 50 --no-pager"
    exit 1
}
python3 - "$PI_HOST" <<'PY'
import asyncio, json, sys
try:
    import websockets
except ImportError:
    print("  (local python has no 'websockets' - skipping end-to-end check)")
    sys.exit(0)

async def check(host):
    async with websockets.connect(f"ws://{host}:5002") as ws:
        data = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        print(f"  Server responding: {data['type']}, version {data['payload']['server_version']}, "
              f"radar_connected={data['payload'].get('radar_connected')}")

asyncio.run(check(sys.argv[1]))
PY
echo ""
echo "=== Deployment Complete ==="
