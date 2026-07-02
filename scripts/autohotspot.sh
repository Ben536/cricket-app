#!/bin/bash
#
# CricketRadar auto-hotspot
#
# Runs ONCE at boot. If a known client WiFi (home / iPhone hotspot) connects
# within ~45s, do nothing (we're at home / have a hotspot). If none connects
# (i.e. we're at the nets), bring up the "CricketRadar" access point.
#
# Because it only runs at boot, there is NO mid-session flapping:
#   - at home  -> home WiFi connects -> no AP
#   - at nets  -> nothing connects   -> AP comes up
# The CricketRadar connection's own autoconnect is left OFF so that ONLY this
# script ever activates it (NM won't jump to it on a transient home-WiFi blip).
#
set -u
AP="CricketRadar"
IFACE="wlan0"

log() { logger -t autohotspot "$1"; echo "autohotspot: $1"; }

# Poll up to ~45s for wlan0 to connect to a non-AP (station/client) network.
for i in $(seq 1 15); do
  state=$(nmcli -t -f GENERAL.STATE device show "$IFACE" 2>/dev/null | cut -d: -f2)
  conn=$(nmcli -t -f GENERAL.CONNECTION device show "$IFACE" 2>/dev/null | cut -d: -f2)
  if echo "$state" | grep -q "connected" && [ -n "$conn" ] && [ "$conn" != "$AP" ]; then
    log "connected to '$conn' - no access point needed"
    exit 0
  fi
  sleep 3
done

log "no known WiFi after ~45s - starting '$AP' access point"
if nmcli connection up "$AP"; then
  log "'$AP' access point is up (10.42.0.1)"
else
  log "ERROR: failed to bring up '$AP'"
  exit 1
fi
