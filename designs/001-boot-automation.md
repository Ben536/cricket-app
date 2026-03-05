# Design Document: Boot Automation & Standalone Operation

> **Status:** DRAFT - Awaiting Review
> **Classification:** Large
> **Author:** Architect
> **Date:** 2026-03-05

---

## 1. Goal

**What:** Enable CricketRadar to operate as a standalone device that requires no laptop, SSH access, or manual intervention after power-on.

**Why:**
- Current system requires technical knowledge to operate
- Testing sessions are blocked when things don't auto-start
- User cannot debug in the field (at cricket nets)
- Reliability is essential for real-world use

**Success looks like:** User connects power bank → waits 60 seconds → opens phone app → system works. Every time.

---

## 2. System Analysis

### Current Boot Sequence (Manual)

```
Power On
    │
    ▼
Pi boots (30-45s)
    │
    ▼
User SSHs in ──────────────────┐
    │                          │
    ▼                          │ MANUAL
python3 send_config.py         │ INTERVENTION
    │                          │ REQUIRED
    ▼                          │
python3 -m server.websocket    │
    │                          │
    ▼ ─────────────────────────┘
System ready
```

### Target Boot Sequence (Automated)

```
Power On
    │
    ▼
Pi boots (30-45s)
    │
    ├─────────────────────────────────┐
    │                                 │
    ▼                                 ▼
Network connects              Watchdog starts
(to phone hotspot)            monitoring
    │                                 │
    ▼                                 │
Radar config service starts           │
    │                                 │
    ├── Retry on failure ◄────────────┤
    │                                 │
    ▼                                 │
WebSocket server starts               │
    │                                 │
    ├── Restart on crash ◄────────────┘
    │
    ▼
mDNS advertises cricketradar.local
    │
    ▼
System ready (LED green)
```

---

## 3. Components

### 3.1 Radar Configuration Service

**Purpose:** Configure radar hardware on boot

**File:** `/etc/systemd/system/cricket-radar.service`

**Behaviour:**
- Runs once after network is available
- Waits for `/dev/ttyUSB0` to exist (radar connected)
- Sends `profile_cricket.cfg` to radar
- Retries up to 5 times with exponential backoff
- Logs all attempts to journald
- Sets exit code for dependent services

**Dependencies:**
- Requires: `dev-ttyUSB0.device`
- After: `network-online.target`
- Before: `cricket-server.service`

### 3.2 WebSocket Server Service

**Purpose:** Run the WebSocket server that clients connect to

**File:** `/etc/systemd/system/cricket-server.service`

**Behaviour:**
- Starts after radar config completes (or times out)
- Auto-restarts on crash (max 5 restarts per 60 seconds)
- Logs to journald with structured output
- Graceful shutdown on SIGTERM

**Dependencies:**
- Requires: `cricket-radar.service`
- After: `cricket-radar.service`

### 3.3 Health Monitor Script

**Purpose:** Detect stuck states, provide recovery

**File:** `/home/bdrysdale/cricket-app/scripts/health_monitor.py`

**Behaviour:**
- Runs as separate service
- Checks every 30 seconds:
  - Is radar responding? (serial port test)
  - Is WebSocket server accepting connections?
  - Is network connected?
- On failure:
  - Log detailed diagnostics
  - Attempt recovery (restart service)
  - After 3 failures: reboot device
- Provides status via GPIO LED (optional)

### 3.4 Network Configuration

**Purpose:** Auto-connect to known networks

**Approach:** NetworkManager with connection priorities

**Configuration:**
```
Network Priority:
1. "CricketRadar-Hotspot" (phone hotspot) - priority 100
2. Home WiFi - priority 50
3. Any open network - priority 0 (disabled for security)
```

**Fallback:** If no network after 120 seconds, continue anyway (radar still works locally via USB connection if Pi has AP mode enabled later)

### 3.5 Service Discovery

**Purpose:** Allow app to find Pi without knowing IP

**Approach:** mDNS via Avahi (already installed on Raspberry Pi OS)

**Configuration:**
- Hostname: `cricketradar`
- Advertised services:
  - `_http._tcp` on port 5003 (REST API)
  - `_cricketradar._tcp` on port 5002 (WebSocket)

### 3.6 PWA Infrastructure

**Purpose:** Installable app experience on phone

**Components:**
- `public/manifest.json` - App metadata
- `public/sw.js` - Service worker for offline caching
- `public/icons/` - App icons (192x192, 512x512)
- Updated `index.html` - manifest link, SW registration

---

## 4. Failure Modes & Mitigations

| Failure | Detection | Mitigation | Recovery |
|---------|-----------|------------|----------|
| **Radar not connected** | `/dev/ttyUSB0` missing | Service waits with timeout | Retry every 10s for 2 mins, then start server anyway (manual mode) |
| **Radar config fails** | Non-zero exit code | Log error, retry with backoff | After 5 retries, start server anyway |
| **Radar config hangs** | Timeout (30s) | Kill process | Retry, then continue |
| **Server crash** | Process exits | systemd auto-restart | Max 5 restarts/minute, then stop (prevents crash loop) |
| **Server unresponsive** | Health check fails | Log diagnostics | Restart server, if persists, restart radar config too |
| **WiFi not connecting** | No IP after 120s | Continue without network | User must check phone hotspot is on |
| **mDNS not resolving** | App can't find Pi | App falls back to IP scan | Manual IP entry always available |
| **Power fluctuation** | Unexpected reboot | Journald logs survive reboot | Automatic restart of all services |
| **SD card corruption** | Boot failure | Unrecoverable | User must reflash (document recovery process) |

---

## 5. Security Considerations

| Risk | Severity | Mitigation |
|------|----------|------------|
| Open WebSocket port | Medium | Only bind to local networks, not 0.0.0.0 in production |
| Service runs as root | Low | Services run as `bdrysdale` user, not root |
| Credentials in config | Low | Phone hotspot password in NetworkManager (not in code) |
| No authentication | Medium | Acceptable for prototype (single user on local network) |
| Firmware update vector | Low | Not implementing OTA updates yet |

**Decision:** Security is acceptable for Tier 1 (prototype). Authentication deferred to Tier 2.

---

## 6. Configuration Management

All configuration in one place: `/home/bdrysdale/cricket-app/config/`

```
config/
├── radar.cfg          # Radar hardware config (symlink to active profile)
├── profiles/
│   ├── cricket.cfg    # High-velocity cricket config
│   ├── default.cfg    # Original TI demo config
│   └── test.cfg       # Low-power test config
├── server.env         # Server environment variables
└── network.conf       # Network priorities (documentation only)
```

**Switching profiles:** Change symlink, restart radar service.

---

## 7. Logging & Diagnostics

**All services log to journald:**
```bash
# View radar config logs
journalctl -u cricket-radar -f

# View server logs
journalctl -u cricket-server -f

# View all cricket logs
journalctl -u 'cricket-*' --since "10 minutes ago"

# View boot sequence
journalctl -b
```

**Log levels:**
- INFO: Normal operation, state changes
- WARNING: Recoverable issues, retries
- ERROR: Failures requiring attention
- DEBUG: Detailed diagnostics (disabled by default)

**Structured fields:**
```python
logger.info("Radar configured", extra={
    "config_file": "cricket.cfg",
    "duration_ms": 1234,
    "retry_count": 0
})
```

---

## 8. Components Affected

### New Files
| File | Purpose |
|------|---------|
| `/etc/systemd/system/cricket-radar.service` | Radar config service |
| `/etc/systemd/system/cricket-server.service` | WebSocket server service |
| `scripts/health_monitor.py` | Health checking and recovery |
| `scripts/configure_radar.py` | Robust radar configuration script |
| `config/radar.cfg` | Active radar config symlink |
| `public/manifest.json` | PWA manifest |
| `public/sw.js` | Service worker |

### Modified Files
| File | Change |
|------|--------|
| `src/hooks/useServerSimulation.ts` | Add auto-discovery logic |
| `src/App.tsx` | Add connection status, discovery UI |
| `index.html` | Add manifest link, SW registration |
| `CRICKETRADAR_PLAN.md` | Update status, add new components |

### Pi System Changes
| Change | Command |
|--------|---------|
| Set hostname | `hostnamectl set-hostname cricketradar` |
| Add phone hotspot | `nmcli dev wifi connect ...` |
| Enable services | `systemctl enable cricket-radar cricket-server` |

---

## 9. Acceptance Criteria

### Must Have (P0)
- [ ] Pi boots and configures radar without SSH access
- [ ] WebSocket server starts automatically
- [ ] Server restarts automatically if it crashes
- [ ] Radar config retries if it fails
- [ ] Pi connects to phone hotspot automatically
- [ ] App can discover Pi via `cricketradar.local`
- [ ] All services log to journald
- [ ] System recovers from power cycle

### Should Have (P1)
- [ ] Health monitor detects stuck states
- [ ] Health monitor triggers recovery
- [ ] PWA installable on phone
- [ ] PWA works offline (cached assets)
- [ ] App shows clear connection status
- [ ] App shows "searching..." during discovery

### Nice to Have (P2)
- [ ] GPIO LED indicates system status
- [ ] Multiple radar profiles switchable
- [ ] Boot time under 45 seconds
- [ ] Detailed diagnostics endpoint

---

## 10. Test Strategy

### Unit Tests
- `configure_radar.py`: Mock serial port, test retry logic
- `health_monitor.py`: Mock services, test detection and recovery

### Integration Tests
- Service startup sequence (radar → server)
- Service restart on crash (kill -9, verify restart)
- Network reconnection (disconnect WiFi, verify reconnect)

### System Tests
1. **Cold boot test:** Power off completely → power on → verify operational
2. **Radar disconnect:** Unplug radar USB → verify graceful degradation → replug → verify recovery
3. **Server crash:** Kill server → verify auto-restart → verify client reconnects
4. **Network loss:** Disable hotspot → verify server continues → enable → verify reconnect
5. **Power cycle:** Unplug power → replug → verify full recovery

### Manual Testing Checklist
- [ ] Boot from cold with power bank only
- [ ] Connect from phone via hotspot
- [ ] Discover Pi automatically
- [ ] Stream radar data
- [ ] Record session
- [ ] Power cycle and repeat
- [ ] Test 5 times consecutively without failure

---

## 11. Rollback Plan

If automation causes issues:

1. **Disable services:**
   ```bash
   sudo systemctl disable cricket-radar cricket-server
   ```

2. **Return to manual mode:**
   ```bash
   cd ~ && python3 send_config.py profile_cricket.cfg
   cd ~/cricket-app && python3 -m server.websocket_server
   ```

3. **Revert hostname:**
   ```bash
   sudo hostnamectl set-hostname raspberrypi
   ```

---

## 12. Implementation Order

| Phase | Components | Est. Effort |
|-------|------------|-------------|
| 1 | `configure_radar.py` script with retry logic | 1 hour |
| 2 | `cricket-radar.service` systemd unit | 30 min |
| 3 | `cricket-server.service` systemd unit | 30 min |
| 4 | Network/hostname configuration | 30 min |
| 5 | Test boot sequence | 1 hour |
| 6 | `health_monitor.py` | 2 hours |
| 7 | App auto-discovery | 2 hours |
| 8 | PWA manifest + service worker | 1.5 hours |
| 9 | System testing | 2 hours |

**Total estimated:** ~11 hours

---

## 13. Open Questions

1. **Should server bind to all interfaces (0.0.0.0) or just local network?**
   - Recommendation: Bind to all for simplicity in prototype

2. **What timeout for radar config before giving up?**
   - Recommendation: 2 minutes (allows time for user to plug in radar)

3. **Should health monitor reboot the Pi, or just restart services?**
   - Recommendation: Restart services first (3x), then reboot as last resort

4. **PWA: Cache all assets or just critical path?**
   - Recommendation: Cache index.html + main JS/CSS only (keep it simple)

---

## 14. Sign-off

| Role | Name | Approved | Date |
|------|------|----------|------|
| Architect | Claude | ✓ | 2026-03-05 |
| Reviewer | *Pending* | | |
| User | *Pending* | | |

---

*This design follows the CricketRadar Ways of Working. No code will be written until this design is approved.*
