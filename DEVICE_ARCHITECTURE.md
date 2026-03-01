# CricketRadar Device Architecture

## Overview

CricketRadar is a standalone cricket training device that tracks ball trajectories using mmWave radar and provides real-time feedback via a mobile app. The device is designed as a "black box" - users plug it in, connect via their phone, and start training. No technical knowledge required.

---

## Hardware Components

```
┌─────────────────────────────────────────────────────────┐
│                    CRICKETRADAR DEVICE                  │
│                   (Bespoke Enclosure)                   │
│                                                         │
│   ┌─────────────────┐      ┌─────────────────────┐     │
│   │  Raspberry Pi   │      │   IWR6843ISK-ODS    │     │
│   │     3B+         │◄────►│   mmWave Radar      │     │
│   │                 │ USB  │   60GHz             │     │
│   │  - WiFi AP      │      │                     │     │
│   │  - WebSocket    │      │   Field of View:    │     │
│   │  - Game Engine  │      │   ±60° azimuth      │     │
│   │  - Database     │      │   ±40° elevation    │     │
│   │                 │      │   0.25m - 9m range  │     │
│   └────────┬────────┘      └─────────────────────┘     │
│            │                                            │
│            │                                            │
│   ┌────────▼────────┐      ┌─────────────────────┐     │
│   │  Battery Pack   │      │   Mounting Clip     │     │
│   │  (Internal)     │      │   (Net/Bar)         │     │
│   │  5V output      │      │                     │     │
│   └─────────────────┘      └─────────────────────┘     │
│                                                         │
│   [  Power Button  ]     [ Status LED (optional) ]     │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### Bill of Materials

| Component | Model | Purpose | Approx Cost |
|-----------|-------|---------|-------------|
| Computer | Raspberry Pi 3B+ | Main processor, WiFi AP | £35 |
| Radar | TI IWR6843ISK-ODS | Ball trajectory tracking | £200 |
| Battery | TBD (5V 3A, 10000mAh+) | Portable power (~2-3 hours) | £25 |
| Enclosure | Bespoke case | Houses all components | £30 |
| Mounting | Clip/clamp mechanism | Attaches to net/bar | £10 |
| **Total** | | | **~£300** |

### Enclosure Notes

- Bespoke case designed to house Pi, radar, and battery
- Mounting clip for attachment to net structure or support bars
- Position above batsman minimises risk of ball impact
- Ventilation required for heat dissipation
- Single power button for on/off
- Optional status LED (power on, ready, error)

---

## Physical Setup

```
                    CRICKET NET / BATTING CAGE

    ════════════════════════════════════════════════════
    │                                                    │
    │                   ┌─────────┐                      │
    │                   │ DEVICE  │  ← Clipped to net/bar│
    │                   │ ◉ Radar │    Looking DOWN      │
    │                   └────┬────┘    Height: ~2.5-3m   │
    │                        │                           │
    │                        │ Radar field of view       │
    │                        ▼                           │
    │                    ┌───────┐                       │
    │                    │BATSMAN│                       │
    │                    │   ▲   │                       │
    │                    └───┼───┘                       │
    │                        │                           │
    │                        │ Ball trajectory           │
    │                        │                           │
    │                        ▼                           │
    │               ○ Bowling Machine / Bowler           │
    │                                                    │
    ════════════════════════════════════════════════════

                    SIDE VIEW:

         Net bar ═══════╦═══════════════════════════
                        ║
                   ┌────╨────┐
                   │ DEVICE  │  2.5-3m height
                   │    ◉────┼──────┐
                   └─────────┘      │ Radar FOV
                        ▼           │ (looking down
                    ┌───────┐       │  at pitch)
                    │BATSMAN│ ◄─────┘
                    └───────┘
                        │
                        ▼
                    ○ Bowler          Ground level
         ══════════════════════════════════════════
```

### Mounting Considerations

- **Position:** Directly above the batsman, clipped to net or support bar
- **Height:** Approximately 2.5-3m above ground
- **Angle:** Pointing downward at the pitch/crease area
- **Attachment:** Clip or clamp to net structure or support bar
- **Protection:** Minimal risk of ball impact (shots rarely go straight up)

---

## Software Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         RASPBERRY PI                                 │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │                    NETWORK LAYER                             │    │
│  │  ┌───────────────┐    ┌───────────────┐                     │    │
│  │  │   hostapd     │    │    dnsmasq    │                     │    │
│  │  │   WiFi AP     │    │   DHCP/DNS    │                     │    │
│  │  │ "CricketRadar"│    │ 192.168.4.x   │                     │    │
│  │  └───────────────┘    └───────────────┘                     │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                              │                                       │
│                              ▼                                       │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │                   APPLICATION LAYER                          │    │
│  │                                                               │    │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │    │
│  │  │  WebSocket  │  │   REST API  │  │   Static Files      │  │    │
│  │  │   Server    │  │   Server    │  │   (React App)       │  │    │
│  │  │   :5002     │  │   :5003     │  │   :80               │  │    │
│  │  └──────┬──────┘  └──────┬──────┘  └─────────────────────┘  │    │
│  │         │                │                                    │    │
│  │         └───────┬────────┘                                    │    │
│  │                 │                                             │    │
│  │         ┌───────▼───────┐                                     │    │
│  │         │    Message    │                                     │    │
│  │         │    Router     │                                     │    │
│  │         └───────┬───────┘                                     │    │
│  │                 │                                             │    │
│  │    ┌────────────┼────────────┐                               │    │
│  │    ▼            ▼            ▼                               │    │
│  │ ┌──────┐   ┌──────────┐  ┌──────────┐                       │    │
│  │ │Session│   │  Game    │  │  Radar   │                       │    │
│  │ │Manager│   │  Engine  │  │ Interface│                       │    │
│  │ └───┬──┘   └────┬─────┘  └────┬─────┘                       │    │
│  │     │           │             │                              │    │
│  │     ▼           │             ▼                              │    │
│  │ ┌──────────┐    │      ┌───────────┐                        │    │
│  │ │ SQLite   │◄───┘      │  Serial   │                        │    │
│  │ │ Database │           │ /dev/USB1 │                        │    │
│  │ └──────────┘           └───────────┘                        │    │
│  │                                                              │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### Component Responsibilities

| Component | Port | Responsibility |
|-----------|------|----------------|
| **hostapd** | - | Creates "CricketRadar" WiFi network |
| **dnsmasq** | - | Assigns IP addresses to connected devices |
| **WebSocket Server** | 5002 | Real-time bidirectional communication |
| **REST API** | 5003 | Detailed session history, analytics |
| **Static Server** | 80 | Serves React app (optional, can use Vercel) |
| **Message Router** | - | Validates and routes WebSocket messages |
| **Session Manager** | - | Manages active sessions, player profiles |
| **Game Engine** | - | Physics simulation, shot outcome calculation |
| **Radar Interface** | - | Reads TLV frames, detects ball trajectories |
| **SQLite Database** | - | Persists sessions, deliveries, profiles |

---

## User Experience Flow

### First Time Setup

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                  │
│  1. UNBOX           2. PLUG IN          3. WAIT 30 SEC          │
│                                                                  │
│  ┌─────────┐        ┌─────────┐         ┌─────────┐             │
│  │ ░░░░░░░ │        │ ████████│───⚡    │ ████████│  ✓ Ready   │
│  │ ░░░░░░░ │   →    │ ████████│         │ ████████│             │
│  │ ░░░░░░░ │        │ ████████│         │ ████████│             │
│  └─────────┘        └─────────┘         └─────────┘             │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Connecting (Every Session)

```
┌──────────────────────────────────────────────────────────────────┐
│                         PHONE SCREEN                              │
│                                                                   │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐          │
│  │             │    │   WiFi      │    │             │          │
│  │  Open App   │ →  │  Settings   │ →  │  Connected! │          │
│  │             │    │             │    │             │          │
│  │  [Cricket   │    │ ○ Home WiFi │    │  ┌───────┐  │          │
│  │   Radar]    │    │ ● Cricket   │    │  │ 24-0  │  │          │
│  │             │    │   Radar  ✓  │    │  │ 4.1ov │  │          │
│  │  "Connect   │    │ ○ Neighbor  │    │  └───────┘  │          │
│  │   to device │    │             │    │             │          │
│  │   to start" │    │             │    │  [Start]    │          │
│  │             │    │  Password:  │    │             │          │
│  │  [Connect]  │    │  cricket123 │    │             │          │
│  └─────────────┘    └─────────────┘    └─────────────┘          │
│                                                                   │
│     Step 1              Step 2              Step 3               │
│  Tap Connect       Join CricketRadar     Ready to play!         │
│                       network                                     │
└──────────────────────────────────────────────────────────────────┘
```

### During Session

```
┌──────────────────────────────────────────────────────────────────┐
│                      APP MAIN SCREEN                              │
│                                                                   │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  Player: Ben            Session: 24 runs (32 balls)        │  │
│  │  Strike Rate: 75.0      This Over: ● ● 4 ● 1 ●            │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                   │
│  ┌──────────────────────┐  ┌───────────────────────────────┐    │
│  │                      │  │         WAGON WHEEL           │    │
│  │    FIELD POSITION    │  │              ╱╲               │    │
│  │         ○            │  │            ╱    ╲             │    │
│  │      ○     ○         │  │     ────●─╱──────╲────        │    │
│  │    ○    ▲    ○       │  │          ╲  ◉   ╱             │    │
│  │      ○     ○         │  │           ╲────╱──●           │    │
│  │         ○            │  │            ●                   │    │
│  │                      │  │                                │    │
│  └──────────────────────┘  └───────────────────────────────┘    │
│                                                                   │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  LAST BALL: FOUR! Cover drive, 67m                        │  │
│  │  Exit speed: 28.3 m/s | Angle: 15° | Fielded by: Cover   │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                   │
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐        │
│  │  0   │ │  1   │ │  2   │ │  3   │ │  4   │ │  6   │        │
│  └──────┘ └──────┘ └──────┘ └──────┘ └──────┘ └──────┘        │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐            │
│  │      W       │ │     Wide     │ │   No Ball    │            │
│  └──────────────┘ └──────────────┘ └──────────────┘            │
│                                                                   │
│  Manual input (if radar misses) or auto-detected from radar     │
│                                                                   │
└──────────────────────────────────────────────────────────────────┘
```

---

## Data Flow: Ball Tracking

### Radar → Game Engine → UI

```
                    BALL HIT BY BATSMAN
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│                     RADAR DETECTION                               │
│                                                                   │
│  1. Radar detects moving object in field of view                 │
│  2. Tracks position over multiple frames (10-20ms intervals)     │
│  3. Outputs TLV (Type-Length-Value) data stream                  │
│                                                                   │
│  TLV Frame:                                                       │
│  ┌────────┬────────┬────────┬────────┬────────┐                  │
│  │ Magic  │ Header │ Points │ Targets│  ...   │                  │
│  │ 8 bytes│40 bytes│  var   │  var   │        │                  │
│  └────────┴────────┴────────┴────────┴────────┘                  │
│                                                                   │
│  Each point: x, y, z (meters), doppler (m/s)                     │
│                                                                   │
└───────────────────────────┬──────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│                    BALL DETECTION                                 │
│                                                                   │
│  Two potential approaches:                                        │
│                                                                   │
│  APPROACH A: Rule-Based Filtering                                 │
│  ────────────────────────────────                                 │
│  1. Filter points by velocity (ball moves fast: >10 m/s)         │
│  2. Filter by direction (moving away from batsman position)      │
│  3. Filter by size (ball = single point or small cluster)        │
│  4. Track across frames to build trajectory                      │
│  + Simple, fast, no training required                            │
│  - Bat swing and hands also move fast (similar velocity to ball) │
│  - May struggle with noise, batsman movement, net interference   │
│                                                                   │
│  APPROACH B: ML-Based Detection                                   │
│  ─────────────────────────────────────────────                    │
│  Train a model to recognise ball vs non-ball signatures:         │
│                                                                   │
│  Training data collection:                                        │
│  - Session 1: Bowl 100+ balls, no batsman → label as "ball"      │
│  - Session 2: Batsman shadow batting → label as "not ball"       │
│  - Session 3: Ball + batsman → validation set                    │
│                                                                   │
│  Model learns to distinguish:                                     │
│  - Ball trajectory (fast, small, consistent path)                │
│  - Batsman movement (large, slower, complex motion)              │
│  - Net movement (stationary or oscillating)                      │
│  - Background noise (random, low velocity)                       │
│                                                                   │
│  + More robust in noisy environments                              │
│  + Can improve over time with more data                          │
│  - Requires training data collection                              │
│  - More complex to implement and maintain                        │
│                                                                   │
│  RECOMMENDATION: Start with Approach A, add ML if needed         │
│                                                                   │
│  ─────────────────────────────────────────────                    │
│  Once ball is identified, calculate:                              │
│  - Exit speed (velocity at bat contact point)                    │
│  - Horizontal angle (direction: cover, mid-wicket, etc.)         │
│  - Vertical angle (lofted vs along ground)                       │
│  - Projected landing position                                     │
│                                                                   │
└───────────────────────────┬──────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│                     GAME ENGINE                                   │
│                                                                   │
│  Input:                                                           │
│  - Exit speed, angles from radar                                  │
│  - Field positions (from UI)                                      │
│  - Difficulty setting                                             │
│                                                                   │
│  Processing:                                                      │
│  1. Calculate full trajectory (aerial + rolling)                  │
│  2. Check boundary (>70m or aerial at edge = 4/6)                │
│  3. Find closest fielder who can intercept                       │
│  4. Simulate fielding outcome (catch/stop/misfield)              │
│  5. Calculate runs based on fielding time                        │
│                                                                   │
│  Output:                                                          │
│  - Outcome: dot, 1, 2, 3, 4, 6, W (caught), etc.                │
│  - Description: "Cover drive, fielded at cover, 1 run"          │
│  - Trajectory data for wagon wheel                               │
│                                                                   │
└───────────────────────────┬──────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│                    UI UPDATE                                      │
│                                                                   │
│  Via WebSocket:                                                   │
│  1. shot_result message with outcome                             │
│  2. wagon_wheel_update with trajectory for visualization         │
│  3. session_state with updated score                             │
│                                                                   │
│  UI displays:                                                     │
│  - Score update with animation                                    │
│  - New line on wagon wheel                                        │
│  - Shot description                                               │
│  - Fielder position highlight                                     │
│                                                                   │
└──────────────────────────────────────────────────────────────────┘
```

---

## WiFi Access Point Configuration

### Network Details

| Setting | Value |
|---------|-------|
| SSID | CricketRadar |
| Password | cricket123 (or open) |
| IP Range | 192.168.4.1 - 192.168.4.20 |
| Device IP | 192.168.4.1 |
| Channel | Auto (or 6 for 2.4GHz) |

### Why Access Point Mode?

1. **Works anywhere** - Cricket grounds, parks, backyards - no existing WiFi needed
2. **Zero configuration** - User just connects to a WiFi network
3. **Predictable address** - App always connects to 192.168.4.1
4. **No internet required** - Fully offline operation
5. **Simple mental model** - "Connect to CricketRadar to use CricketRadar"

### Limitations

- User's phone temporarily loses internet (acceptable during training)
- Pi's WiFi chip has limited range (~10-15m)
- Only ~5 devices can connect reliably

---

## Boot Sequence

```
POWER ON
    │
    ▼
┌─────────────────────────────────────┐
│  Raspberry Pi boots (30-45 sec)     │
│  - Load Linux                        │
│  - Start system services             │
└──────────────────┬──────────────────┘
                   │
                   ▼
┌─────────────────────────────────────┐
│  Start WiFi Access Point            │
│  - hostapd creates "CricketRadar"   │
│  - dnsmasq provides DHCP            │
└──────────────────┬──────────────────┘
                   │
                   ▼
┌─────────────────────────────────────┐
│  Start Radar                        │
│  - Open serial connection           │
│  - Send configuration               │
│  - Verify data streaming            │
└──────────────────┬──────────────────┘
                   │
                   ▼
┌─────────────────────────────────────┐
│  Start Application Services         │
│  - WebSocket server (:5002)         │
│  - REST API (:5003)                 │
│  - Static file server (:80)         │
└──────────────────┬──────────────────┘
                   │
                   ▼
         READY FOR CONNECTIONS
         (Total: ~45-60 seconds)
```

---

## App Connection Flow

### From Browser/PWA

```javascript
// App startup logic (simplified)

1. Check if on "CricketRadar" network
   - Try to reach 192.168.4.1:5002

2. If reachable:
   - Connect WebSocket
   - Request session_state
   - Show main UI

3. If not reachable:
   - Show "Connect to CricketRadar WiFi" screen
   - Provide button to open WiFi settings
   - Poll until connection succeeds
```

### Connection States

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│              │     │              │     │              │
│  NOT ON      │────►│  CONNECTING  │────►│  CONNECTED   │
│  NETWORK     │     │              │     │              │
│              │     │              │     │              │
└──────────────┘     └──────┬───────┘     └──────┬───────┘
       ▲                    │                    │
       │                    │ timeout            │ disconnect
       │                    ▼                    │
       │             ┌──────────────┐            │
       └─────────────│    ERROR     │◄───────────┘
                     │              │
                     └──────────────┘
```

---

## Database Schema (Summary)

```sql
-- Core tables
players         -- Player profiles (name, batting hand)
sessions        -- Training sessions (date, total runs, etc.)
deliveries      -- Individual balls (trajectory, outcome, radar data)

-- Multi-user support
users           -- User accounts (email, password hash)
auth_tokens     -- Session tokens for authentication

-- Operational
active_sessions -- Currently active WebSocket sessions
```

See `db/migrations/` for full schema.

---

## Manual Input Fallback

The radar may miss balls due to:
- Ball outside field of view
- Multiple balls in air (e.g., throwdowns)
- Net/obstacle interference
- Very slow deliveries

The app provides manual input buttons:
- 0, 1, 2, 3, 4, 6 runs
- W (wicket)
- Wide, No Ball

Manual inputs are marked in the database (`is_manual_input = true`) for analysis.

---

## Future Enhancements

### Phase 2: Cloud Sync
- Sync sessions to cloud when internet available
- View history from any device
- Share sessions with coach

### Phase 3: Video Integration
- Sync with phone camera recording
- Auto-clip highlights based on radar events

### Phase 4: Multiple Radar Units
- Second radar for bowling speed
- Bowling analysis mode

### Phase 5: Team/Club Features
- Multiple player profiles
- Team analytics
- Leaderboards

---

## Technical Specifications

### Radar Performance

| Metric | Value |
|--------|-------|
| Update rate | 10 Hz (100ms per frame) |
| Range | 0.25m - 9m |
| Velocity detection | Up to 40 m/s |
| Angular resolution | ~15° |
| Latency | <100ms from hit to detection |

### Server Performance

| Metric | Target |
|--------|--------|
| WebSocket latency | <50ms |
| Concurrent clients | 5 |
| Session storage | 1000+ sessions |
| Uptime | Boot to ready in <60s |

### App Compatibility

**Current (v1): Browser-based**

| Platform | Support |
|----------|---------|
| iOS Safari | Full (iOS 14+) |
| Android Chrome | Full (Android 8+) |
| Desktop Chrome | Full |
| Desktop Safari | Full |
| Firefox | Full |

**Future: Native Apps**

| Platform | Benefits |
|----------|----------|
| iOS App | Better UX, push notifications, offline support, App Store presence |
| Android App | Better UX, push notifications, offline support, Play Store presence |

Native apps would also enable:
- Bluetooth device discovery (no WiFi switching required)
- Background operation
- Better performance
- Camera integration for video recording

---

## File Structure (Pi)

```
/home/pi/cricket-app/
├── server/
│   ├── websocket_server.py     # Main WebSocket server
│   ├── connection_manager.py   # Client connections
│   ├── message_router.py       # Message handling
│   ├── session_manager.py      # Session state
│   ├── handlers.py             # Message handlers
│   └── radar_interface.py      # Radar communication [TODO]
├── engine/
│   └── game_engine.py          # Physics simulation
├── db/
│   ├── repository.py           # Data access layer
│   ├── cricket.db              # SQLite database
│   └── migrations/             # Schema migrations
├── contracts/
│   ├── api_types.py            # Type definitions
│   └── websocket_protocol.json # Message schema
└── scripts/
    ├── start.sh                # Startup script [TODO]
    ├── setup-ap.sh             # Access point setup [TODO]
    └── configure-radar.sh      # Radar init [TODO]
```

---

## Summary

CricketRadar is a self-contained cricket training device that:

1. **Tracks balls** using 60GHz mmWave radar mounted above the batsman
2. **Simulates outcomes** using physics-based game engine
3. **Displays results** in real-time on a mobile app
4. **Works anywhere** via built-in WiFi access point and battery power
5. **Requires no technical knowledge** to operate

The user experience is:
1. Clip device to net above batting position
2. Press power button
3. Connect phone to "CricketRadar" WiFi
4. Open app
5. Start batting

All complexity is hidden inside the black box.

---

## Testing Plan

### Phase 1: Ball Identification Testing (CRITICAL)

Before building detection algorithms, we need to understand what the radar actually sees.

**Test 1: Ball Only (No Batsman)**
```
Setup: Mount radar above crease, bowl 20+ balls
Record: Raw radar data for each delivery
Analyse:
- What does a ball look like? (single point? cluster?)
- How many frames does it appear in?
- What velocity range?
- Does spin/seam affect signature?
- Consistency across different deliveries?
```

**Test 2: Batsman Only (No Ball)**
```
Setup: Same position, batsman plays shadow shots
Record: Raw radar data during bat swings
Analyse:
- What does a bat swing look like?
- What do hands/arms look like?
- Velocity range of bat swing?
- How to distinguish from ball?
```

**Test 3: Ball + Batsman**
```
Setup: Real batting scenario
Record: Raw radar data during actual shots
Analyse:
- Can we clearly see ball separate from batsman?
- What happens at moment of contact?
- How does ball trajectory appear post-contact?
```

**Decision Point:** Based on Test 1-3 results:
- If ball signature is clearly distinct → Rule-based approach
- If ball/bat signatures overlap significantly → ML approach needed

### Phase 2: Integration Testing

Once ball detection works:
- Connect radar → game engine → UI
- Test full loop with manual input fallback
- Measure detection accuracy (% of balls correctly identified)

### Phase 3: Field Testing

Real-world testing at cricket nets:
- Different net structures
- Different lighting conditions
- Multiple users
- Edge cases (missed shots, edges, leaves)

---

## Open Questions

1. **Ball signature** - What does a ball actually look like from above? (Answer via testing)
2. **Ball vs bat** - Can rule-based distinguish them? (Answer via testing)
3. **Battery life** - What capacity needed for 2-3 hour session?
4. **Coordinate transform** - Radar sees x/y/z from above; need to convert to pitch coordinates
5. **Multiple balls** - How to handle throwdown scenarios with multiple balls in flight?
6. **Calibration** - Does device need calibration for each net setup?
