# CricketRadar - Master Project Plan

> **Purpose:** This document is the single source of truth for the CricketRadar project. It defines where we are, where we're going, and what needs to be done.

---

## Vision

**CricketRadar** is a portable cricket training device that tracks ball trajectories using radar and provides instant feedback via a mobile app.

**Long-term goal:** A consumer product that anyone can buy, set up in minutes, and use to track their batting sessions - with all data syncing to their personal cloud account.

**Short-term goal:** A working prototype that lets me and my friends have fun at the nets and look at our own data.

---

## Product Tiers

### Tier 1: Prototype (Current Focus)
*"Works for me and my friends"*

- Single device, shared by a small group
- Simple player profiles (names only)
- Local database on device
- Browser-based UI
- Manual setup acceptable
- Some rough edges OK

### Tier 2: Beta Product
*"Works reliably, ready for early adopters"*

- Polished UX (quick profile switching, clear feedback)
- PWA (installable, offline-capable)
- Reliable ball detection
- Session history and basic analytics
- Easy setup (plug in, connect WiFi, go)

### Tier 3: Consumer Product
*"Anyone can buy and use it"*

- Native iOS/Android apps
- Cloud accounts with data sync
- Multi-device support (use any device, data follows you)
- Authorization system for shared devices
- Professional enclosure and packaging
- Support and documentation

---

## Current State

### Hardware

| Component | Status | Notes |
|-----------|--------|-------|
| Raspberry Pi 3B+ | ✅ Have | Main processor |
| IWR6843ISK-ODS Radar | ✅ Have | Flashed, outputting data |
| Battery pack | ✅ Have | For portable power |
| 3D printer | ✅ Access | For custom enclosure |
| Enclosure | ❌ Not built | Design needed |
| Mounting clip | ❌ Not built | For attaching to net/bar |

### Software

| Component | Status | Notes |
|-----------|--------|-------|
| **React Frontend** | ✅ Built | On Vercel, full UI |
| **WebSocket Server** | ✅ Built | Running on Pi, tested |
| **Game Engine** | ✅ Built | Python, physics simulation |
| **Database Schema** | ✅ Built | SQLite, multi-user ready |
| **REST API** | ✅ Built | For detailed data queries |
| **Radar Visualizer** | ✅ Built | Pygame, shows point cloud |
| **Radar Recording UI** | ✅ Built | Record bowling/batting/both, 15s max |
| **Radar → Game Engine** | ❌ Not built | Ball detection algorithm |
| **WiFi Access Point** | ❌ Not configured | hostapd/dnsmasq setup |
| **Boot Automation** | ✅ Built | systemd services, health monitor, auto-recovery |
| **Auto-Discovery** | ✅ Built | mDNS (cricketradar.local), fallback chain |
| **PWA** | ✅ Built | manifest, service worker, installable |
| **Profile Switching UI** | ⚠️ Partial | Exists but needs polish |

### Tested & Working

- ✅ Radar outputs TLV frames at 10Hz
- ✅ Can parse radar data and display points
- ✅ WebSocket server accepts connections
- ✅ Game engine simulates shot outcomes
- ✅ Database stores sessions and deliveries
- ✅ UI displays scores and wagon wheel
- ✅ Frontend → Pi → Frontend round-trip (Phase 0 complete)
- ✅ Server simulation via WebSocket (simulateAsync uses Pi when connected)
- ✅ Boot automation: Pi auto-configures radar and starts server on power-on
- ✅ Server crash recovery: systemd auto-restarts within 3 seconds
- ✅ Health monitor: detects failures, triggers recovery
- ✅ Auto-discovery: app finds Pi via cricketradar.local
- ✅ Boot time: ~35 seconds from power to ready

### Not Yet Tested

- ❌ Ball detection from radar data
- ❌ Full session with radar tracking
- ❌ Device mounted at cricket net

---

## Architecture

### Physical Setup

```
                         NET STRUCTURE
    ════════════════════════════════════════════════════
    │                                                    │
    │              ┌─────────────────┐                   │
    │              │  CRICKETRADAR   │ ← Clipped to bar  │
    │              │  ┌───────────┐  │   ~2.5-3m high    │
    │              │  │  ◉ Radar  │  │   Looking DOWN    │
    │              │  └───────────┘  │                   │
    │              └────────┬────────┘                   │
    │                       │                            │
    │                       ▼ Field of view              │
    │                   ┌───────┐                        │
    │                   │BATSMAN│                        │
    │                   └───┬───┘                        │
    │                       │                            │
    │                       ▼ Ball trajectory            │
    │                                                    │
    │                  ○ Bowler                          │
    ════════════════════════════════════════════════════
```

### System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    CRICKETRADAR DEVICE                       │
│                                                              │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐  │
│  │   Radar      │───►│  Pi 3B+      │◄───│   Battery    │  │
│  │ IWR6843ISK   │USB │              │    │              │  │
│  └──────────────┘    │  ┌────────┐  │    └──────────────┘  │
│                      │  │ WiFi AP│  │                       │
│                      │  │"Cricket│  │                       │
│                      │  │ Radar" │  │                       │
│                      │  └───┬────┘  │                       │
│                      └──────┼───────┘                       │
└─────────────────────────────┼───────────────────────────────┘
                              │
                              │ WiFi
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                      USER'S PHONE                            │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐ │
│  │                    BROWSER / PWA                        │ │
│  │                                                         │ │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐ │ │
│  │  │   Profile   │  │   Session   │  │   Wagon Wheel   │ │ │
│  │  │   Switcher  │  │   Score     │  │   & Analytics   │ │ │
│  │  └─────────────┘  └─────────────┘  └─────────────────┘ │ │
│  │                                                         │ │
│  │  ┌─────────────────────────────────────────────────┐   │ │
│  │  │              Manual Input Buttons                │   │ │
│  │  │   [0] [1] [2] [3] [4] [6] [W] [Wide] [NB]       │   │ │
│  │  └─────────────────────────────────────────────────┘   │ │
│  └────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

### Data Flow

```
BALL HIT
    │
    ▼
┌─────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────┐
│  Radar  │───►│    Ball     │───►│    Game     │───►│   UI    │
│  (TLV)  │    │  Detection  │    │   Engine    │    │ Update  │
└─────────┘    └─────────────┘    └─────────────┘    └─────────┘
                     │                   │
                     │                   ▼
                     │            ┌─────────────┐
                     │            │  Database   │
                     │            │  (SQLite)   │
                     └───────────►│             │
                      Raw data    └─────────────┘
                      stored
```

---

## User Experience

### Prototype UX (Now)

```
1. SETUP (Once per session)
   ├── Clip device to net bar
   ├── Press power button
   ├── Wait ~60 seconds
   └── Device ready

2. CONNECT (Each user)
   ├── Open browser
   ├── Go to app URL (or saved bookmark)
   ├── Connect to "CricketRadar" WiFi when prompted
   └── App shows main screen

3. START SESSION
   ├── Tap your profile name (or create new)
   ├── Tap "Start Session"
   └── Begin batting

4. DURING SESSION
   ├── Hit ball → Radar tracks → Score updates automatically
   ├── Radar misses? → Tap manual input button
   ├── Switch batsman? → Tap profile switcher (quick swap)
   └── View wagon wheel and shot descriptions

5. END SESSION
   ├── Tap "End Session"
   ├── See session summary
   └── Data saved to device
```

### Profile Switching (Key UX Requirement)

Profile switching must be fast and easy - one tap to change who's batting.

```
┌────────────────────────────────────────┐
│  Currently batting: [Ben ▼]            │
│                                        │
│  ┌──────────────────────────────────┐  │
│  │ ● Ben (current)                  │  │
│  │ ○ James                          │  │
│  │ ○ Tom                            │  │
│  │ ○ + Add player                   │  │
│  └──────────────────────────────────┘  │
│                                        │
│  Tap name = instant switch             │
│  Current session continues, new batter │
└────────────────────────────────────────┘
```

### Consumer UX (Future)

```
1. BUY & UNBOX
   ├── Take device out of box
   ├── Download CricketRadar app
   └── Create account (or sign in)

2. FIRST TIME SETUP
   ├── App guides through WiFi connection
   ├── Device auto-configures
   └── Tutorial explains usage

3. AT THE NETS
   ├── Clip device, power on
   ├── Open app → Auto-connects
   ├── Select profile → Start
   └── All data syncs to cloud

4. SHARED DEVICE (e.g., at a club)
   ├── Connect to device
   ├── Sign into your account
   ├── "Start session for [Your Name]?"
   ├── Friend confirms on their phone (or trusted user just starts)
   └── Data goes to your cloud account

5. REVIEW ANYTIME
   ├── Open app (anywhere, any device)
   ├── Sign in
   └── View all your historical data
```

---

## Database Design

### Principles

1. **Store everything** - Raw radar data, calculated values, outcomes. Enables future analysis.
2. **Denormalize for queries** - Pre-calculate common metrics for fast retrieval.
3. **Version everything** - Optimistic locking for multi-user scenarios.
4. **Profile-centric** - All data ultimately belongs to a player profile.

### Schema Overview

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│     players     │     │    sessions     │     │   deliveries    │
├─────────────────┤     ├─────────────────┤     ├─────────────────┤
│ id              │◄────│ player_id       │◄────│ session_id      │
│ name            │     │ id              │     │ id              │
│ batting_hand    │     │ date            │     │ ball_number     │
│ created_at      │     │ total_runs      │     │ outcome         │
│ (future: email) │     │ total_balls     │     │ runs            │
│ (future: cloud) │     │ wickets         │     │ exit_speed      │
└─────────────────┘     │ field_config    │     │ horizontal_angle│
                        │ difficulty      │     │ vertical_angle  │
                        │ is_completed    │     │ landing_x/y     │
                        └─────────────────┘     │ fielder_involved│
                                                │ is_manual_input │
                                                │ radar_raw_data  │
                                                │ created_at      │
                                                └─────────────────┘
```

### Future Cloud Schema Additions

```
┌─────────────────┐     ┌─────────────────┐
│     users       │     │   friendships   │
├─────────────────┤     ├─────────────────┤
│ id              │     │ user_id         │
│ email           │     │ friend_id       │
│ password_hash   │     │ can_start_session│
│ created_at      │     │ created_at      │
└────────┬────────┘     └─────────────────┘
         │
         │ 1:1
         ▼
┌─────────────────┐
│     players     │  (now linked to user account)
├─────────────────┤
│ user_id (FK)    │
│ ...             │
└─────────────────┘
```

### What Gets Stored Per Delivery

| Field | Source | Purpose |
|-------|--------|---------|
| `outcome` | Game engine | dot, 1, 2, 3, 4, 6, W, wd, nb |
| `runs` | Game engine | Runs scored |
| `exit_speed` | Radar/calculated | How fast ball left bat (m/s) |
| `horizontal_angle` | Radar/calculated | Direction of shot (degrees) |
| `vertical_angle` | Radar/calculated | Loft of shot (degrees) |
| `landing_x`, `landing_y` | Game engine | Where ball landed/stopped |
| `fielder_involved` | Game engine | Who fielded it |
| `is_boundary` | Game engine | Was it a boundary? |
| `is_aerial` | Game engine | Was it in the air? |
| `is_manual_input` | System | Was this manually entered? |
| `radar_frames` | Radar | Raw point cloud data (JSON) |
| `description` | Game engine | Human-readable description |
| `created_at` | System | Timestamp |

This enables future analysis like:
- Average exit speed by shot direction
- Wagon wheel patterns
- Scoring zones
- Aerial vs ground shots
- Manual input percentage (radar reliability)

---

## Ball Detection

### The Challenge

The radar (mounted above batsman) sees:
- **Ball** - Small, fast, travels away from batsman after contact
- **Bat swing** - Also fast, but larger and different motion pattern
- **Batsman body** - Large, slower movement
- **Net** - Stationary or slight oscillation

Ball and bat both move fast, so velocity alone isn't enough.

### Approach

**Phase 1: Collect data first, then decide algorithm**

Before writing detection code, we need to see what the radar actually captures:

| Test | Setup | Goal |
|------|-------|------|
| Ball only | Bowl 20+ balls, no batsman | Learn ball signature |
| Batsman only | Shadow batting, no ball | Learn bat/body signature |
| Ball + batsman | Real batting | See if signatures are distinguishable |

**Phase 2: Implement detection**

Based on test results:
- If signatures are clearly different → Rule-based filtering
- If signatures overlap → ML classifier needed

### Rule-Based Approach (if viable)

```python
def detect_ball(frame_points, previous_frames):
    candidates = []
    for point in frame_points:
        # Fast-moving
        if point.velocity < 10:
            continue
        # Moving away from batsman zone (not toward)
        if point.direction_toward_batsman:
            continue
        # Small cluster (1-3 points)
        if cluster_size(point) > 3:
            continue
        # Consistent with previous frame trajectory
        if not matches_trajectory(point, previous_frames):
            continue
        candidates.append(point)
    return best_candidate(candidates)
```

### ML Approach (if needed)

- Small classifier running on Pi
- Input: Point cluster features (size, velocity, direction, position)
- Output: Ball confidence score (0-100%)
- Training data: Collected during testing phase

### Output

Once ball is detected, calculate:
- **Exit speed**: Velocity at first detection after bat contact
- **Horizontal angle**: Direction relative to pitch (0° = straight, +ve = off side)
- **Vertical angle**: Loft (0° = along ground, 90° = straight up)

Feed these to game engine → Get outcome → Update UI.

---

## Implementation Phases

### Phase 0: Integration Test (COMPLETE)
*Goal: Prove the full loop works with manual input*

- [x] Start WebSocket server on Pi
- [x] Connect frontend to Pi (configure server URL)
- [x] Send manual input → Server processes → UI updates
- [x] Verify database stores delivery
- [x] Server simulation wired up (simulateAsync sends to Pi, falls back to local)
- [x] **Success criteria:** Manual input works end-to-end

### Phase 1: Ball Detection Testing
*Goal: Understand what radar sees*

- [x] Build radar recording system (UI + backend)
- [ ] Mount radar at net (above batsman position)
- [ ] Record raw data: balls only
- [ ] Record raw data: batsman only
- [ ] Record raw data: ball + batsman
- [ ] Analyse signatures, decide approach
- [ ] **Success criteria:** Know how to detect ball

### Phase 2: Ball Detection Implementation
*Goal: Automatic ball tracking*

- [ ] Implement `radar_interface.py`
- [ ] Connect radar → detection → game engine
- [ ] Test detection accuracy
- [ ] Add manual input fallback for misses
- [ ] **Success criteria:** >70% balls auto-detected

### Phase 3: Device Packaging
*Goal: Portable, self-contained unit*

- [ ] Configure Pi as WiFi access point
- [x] Create boot scripts (auto-start everything) ✅ systemd services + health monitor
- [ ] Design and 3D print enclosure
- [ ] Add mounting clip mechanism
- [ ] Test battery life
- [x] **Success criteria:** Plug in, power on, works ✅ 35s boot, auto-recovery tested

### Phase 4: UX Polish
*Goal: Smooth experience for friends*

- [ ] Quick profile switching UI
- [ ] Connection flow (detect not on network, guide to WiFi)
- [ ] Session history view
- [ ] Basic analytics (strike rate, wagon wheel)
- [ ] **Success criteria:** Friends can use without help

### Phase 5: PWA Conversion
*Goal: App-like experience without app store*

- [x] Add service worker for offline caching ✅
- [x] Add web manifest for "install to home screen" ✅
- [ ] Optimise for mobile
- [ ] **Success criteria:** Feels like a native app

### Phase 6: Cloud Infrastructure (Future)
*Goal: Data syncs across devices*

- [ ] Design cloud API
- [ ] User authentication system
- [ ] Device ↔ Cloud sync protocol
- [ ] Multi-device session authorization
- [ ] **Success criteria:** Use any device, data follows you

### Phase 7: Native Apps (Future)
*Goal: App Store presence*

- [ ] iOS app (Swift/React Native)
- [ ] Android app (Kotlin/React Native)
- [ ] Bluetooth device discovery
- [ ] Push notifications
- [ ] **Success criteria:** Professional consumer apps

---

## What's Built vs What's Needed

### Frontend (React)

| Feature | Status | Notes |
|---------|--------|-------|
| Scorecard display | ✅ Done | |
| Wagon wheel | ✅ Done | |
| Field editor | ✅ Done | |
| Manual input buttons | ✅ Done | |
| WebSocket connection | ✅ Done | |
| Profile selection | ✅ Done | Needs quick-switch polish |
| Server URL config | ✅ Done | Just added |
| Session history | ⚠️ Partial | UI exists, needs wiring |
| Connection flow | ❌ TODO | "Connect to WiFi" guidance |
| PWA manifest | ❌ TODO | |
| Offline support | ❌ TODO | |

### Backend (Python)

| Feature | Status | Notes |
|---------|--------|-------|
| WebSocket server | ✅ Done | Tested on Pi |
| Message router | ✅ Done | |
| Session manager | ✅ Done | |
| Game engine | ✅ Done | Physics simulation |
| Database repository | ✅ Done | SQLite |
| REST API | ✅ Done | For detailed queries |
| Radar interface | ❌ TODO | Critical path |
| Ball detection | ❌ TODO | Critical path |
| WiFi AP setup | ❌ TODO | |
| Boot scripts | ❌ TODO | |

### Hardware

| Feature | Status | Notes |
|---------|--------|-------|
| Pi + Radar working | ✅ Done | Confirmed with visualizer |
| Enclosure design | ❌ TODO | |
| Mounting mechanism | ❌ TODO | |
| Battery integration | ❌ TODO | Have battery, need integration |

---

## Immediate Next Steps

1. **Integration test (Phase 0)**
   - Configure frontend to connect to Pi's WebSocket server
   - Test manual input → game engine → UI update
   - Verify full loop works

2. **Ball detection testing (Phase 1)**
   - Mount radar at net
   - Record data during bowling/batting
   - Analyse what we're working with

3. **Ball detection implementation (Phase 2)**
   - Build `radar_interface.py`
   - Connect radar to game engine
   - Test with real balls

---

## Key Decisions Made

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Radar position | Above batsman | Clear view of ball trajectory post-contact |
| Network mode | Pi as WiFi AP | Works anywhere, no existing WiFi needed |
| UI platform (now) | Browser/Vercel | Already built, works well enough |
| UI platform (future) | PWA → Native | Incremental path to app store |
| Database | SQLite on device | Simple, portable, sufficient for prototype |
| Database (future) | Cloud sync | Needed for multi-device |
| Ball detection | Test first, then decide | Need data before choosing algorithm |
| Profile model | Simple names (now) | Cloud accounts can be added later |

---

## Open Questions

1. **Ball detection** - Will rule-based work or do we need ML? (Answer: test first)
2. **Battery life** - How long does it last? Need to measure.
3. **Radar accuracy** - What % of balls will it detect? (Target: >70%)
4. **Net interference** - Does the net structure affect radar? Need to test.
5. **Multiple batsmen** - What happens during changeover? (Handle in UI)

---

## Success Metrics

### Prototype (Tier 1)

| Metric | Target |
|--------|--------|
| Boot to ready | <90 seconds |
| Ball detection rate | >50% (rest manual) |
| Session recorded correctly | 100% |
| Friends can use it | Yes, with minimal help |
| Fun to use | Yes |

### Beta (Tier 2)

| Metric | Target |
|--------|--------|
| Ball detection rate | >80% |
| Setup time | <2 minutes |
| Battery life | >2 hours |
| Works first time | Yes |

### Consumer (Tier 3)

| Metric | Target |
|--------|--------|
| Ball detection rate | >90% |
| Setup time | <5 minutes |
| Works out of box | Yes |
| Data syncs reliably | Yes |
| App store rating | >4.0 |

---

---

# PART 2: TECHNICAL REFERENCE

---

## System Credentials & Access

### Raspberry Pi

| Property | Value |
|----------|-------|
| Hostname | `raspberrypi` |
| mDNS Address | `raspberrypi.local` |
| Current IP | `192.168.0.191` (may change on network) |
| Username | `bdrysdale` |
| Password | `Radarcricket12$` |
| SSH Command | `ssh bdrysdale@raspberrypi.local` |

### Ports & Services

| Service | Port | Protocol | Purpose |
|---------|------|----------|---------|
| WebSocket Server | 5002 | WS | Real-time UI ↔ Pi communication |
| REST API | 5003 | HTTP | Detailed data queries, analytics |
| Static Files (future) | 80 | HTTP | Serve React app from Pi |

### Radar Serial Ports

| Port | Baud Rate | Purpose |
|------|-----------|---------|
| `/dev/ttyUSB0` | 115200 | CLI/Configuration port |
| `/dev/ttyUSB1` | 921600 | Data output port (TLV frames) |

---

## File Locations

### On Development Machine (Mac)

```
/Users/Ben/Documents/cricket-app/
├── src/                      # React frontend
│   ├── App.tsx               # Main UI component
│   ├── App.css               # Styles
│   ├── api/
│   │   ├── websocket.ts      # WebSocket client
│   │   ├── config.ts         # Server URL configuration
│   │   └── hooks/
│   │       ├── useGameState.ts   # Server state sync
│   │       ├── useGameActions.ts # Send commands
│   │       └── useConnection.ts  # Connection status
│   └── components/
│       └── ServerConfig.tsx  # Server URL settings UI
├── server/                   # Python backend
├── engine/                   # Game physics engine
├── db/                       # Database layer
├── contracts/                # API type definitions
└── CRICKETRADAR_PLAN.md      # This file
```

### On Raspberry Pi

```
/home/bdrysdale/cricket-app/
├── server/
│   ├── websocket_server.py     # Main server entry point
│   ├── connection_manager.py   # Client connection tracking
│   ├── message_router.py       # Message validation & routing
│   ├── session_manager.py      # Session state management
│   ├── handlers.py             # Message handlers
│   └── rest_api.py             # HTTP API
├── engine/
│   └── game_engine.py          # Physics simulation
├── db/
│   ├── repository.py           # Data access layer
│   ├── database.py             # Legacy DB interface
│   ├── cricket.db              # SQLite database
│   └── migrations/             # Schema migrations
├── contracts/
│   ├── api_types.py            # Python type definitions
│   └── websocket_protocol.json # Message schema
├── profile_3d.cfg              # Radar configuration file
├── radar_live_view.py          # Radar visualizer (pygame)
└── send_config.py              # Send config to radar
```

---

## How the Systems Connect

### Current Data Flow (Manual Input)

```
┌─────────────┐         ┌─────────────┐         ┌─────────────┐
│   Browser   │   WS    │     Pi      │         │   SQLite    │
│  (Vercel)   │◄──────►│   Server    │◄───────►│  Database   │
│             │  :5002  │             │         │             │
└─────────────┘         └──────┬──────┘         └─────────────┘
                               │
                               ▼
                        ┌─────────────┐
                        │    Game     │
                        │   Engine    │
                        └─────────────┘
```

**Step-by-step:**
1. User opens app in browser (served from Vercel)
2. Browser connects to `ws://192.168.0.191:5002` (or `raspberrypi.local`)
3. User taps "4 runs" button
4. Browser sends `manual_input` message via WebSocket
5. Pi server receives, routes to handler
6. Handler calls game engine with shot parameters
7. Game engine calculates outcome
8. Handler saves delivery to SQLite database
9. Handler sends `shot_result` back via WebSocket
10. Browser updates UI (score, wagon wheel)

### Target Data Flow (Radar Tracking)

```
┌─────────────┐         ┌─────────────┐         ┌─────────────┐
│   Browser   │   WS    │     Pi      │         │   SQLite    │
│             │◄──────►│   Server    │◄───────►│  Database   │
│             │  :5002  │             │         │             │
└─────────────┘         └──────┬──────┘         └─────────────┘
                               │
                    ┌──────────┴──────────┐
                    │                     │
                    ▼                     ▼
             ┌─────────────┐       ┌─────────────┐
             │    Game     │       │   Radar     │
             │   Engine    │◄──────│  Interface  │
             └─────────────┘       └──────┬──────┘
                                          │
                                          │ Serial
                                          ▼
                                   ┌─────────────┐
                                   │   Radar     │
                                   │ IWR6843ISK  │
                                   └─────────────┘
```

**Step-by-step (target):**
1. Ball is hit by batsman
2. Radar detects movement, outputs TLV data on `/dev/ttyUSB1`
3. Radar interface reads serial data, parses TLV frames
4. Ball detection algorithm identifies cricket ball
5. Trajectory calculated: exit speed, angles
6. Game engine called with radar-derived parameters
7. Outcome calculated, saved to database
8. Result sent to browser via WebSocket
9. UI updates automatically

---

## WebSocket Protocol

### Message Format

All messages follow this structure:

```json
{
  "type": "message_type",
  "message_id": "uuid-v4",
  "timestamp": "2026-03-01T10:30:00.000Z",
  "in_reply_to": "optional-uuid",
  "payload": { ... }
}
```

### Client → Server Messages

| Type | Purpose | Key Payload Fields |
|------|---------|-------------------|
| `ping` | Heartbeat | (none) |
| `start_session` | Begin new session | `player_id` |
| `end_session` | Finish session | `session_id` |
| `manual_input` | Record a ball | `session_id`, `outcome`, `runs` |
| `set_field` | Update fielder positions | `fielders[]` |
| `set_difficulty` | Change difficulty | `difficulty` |
| `select_profile` | Switch active player | `profile_id` |
| `create_profile` | New player | `name`, `batting_hand` |
| `undo` | Remove last ball | `session_id` |

### Server → Client Messages

| Type | Purpose | Key Payload Fields |
|------|---------|-------------------|
| `pong` | Heartbeat response | (none) |
| `session_state` | Full state snapshot | `session`, `profiles`, `difficulty`, `field_config` |
| `shot_result` | Ball outcome | `outcome`, `runs`, `description`, `end_position` |
| `wagon_wheel_update` | New shot for display | `shot` (coordinates, outcome) |
| `ball_tracking` | Real-time radar data | `frame_number`, `points[]` |
| `error` | Error occurred | `code`, `message`, `recoverable` |

---

## Radar Configuration

### TLV Frame Structure

```
┌────────┬────────┬────────┬────────┬────────┐
│ Magic  │ Header │ TLV 1  │ TLV 2  │  ...   │
│ 8 bytes│40 bytes│  var   │  var   │        │
└────────┴────────┴────────┴────────┴────────┘

Magic bytes: 02 01 04 03 06 05 08 07

Header (40 bytes after magic):
- Bytes 0-3:   Version
- Bytes 4-7:   Total packet length
- Bytes 8-11:  Platform (0x6843 for IWR6843)
- Bytes 12-15: Frame number
- Bytes 16-19: CPU time
- Bytes 20-23: Number of detected objects
- Bytes 24-27: Number of TLVs

TLV structure:
- Bytes 0-3: Type
- Bytes 4-7: Length
- Bytes 8+:  Data

TLV Type 1 (Detected Points):
- Each point: x (float), y (float), z (float), doppler (float) = 16 bytes
```

### Radar Config File (profile_3d.cfg)

Key parameters:
- Frame rate: 10 Hz (100ms per frame)
- Range: 0.25m - 9m
- Velocity: Up to 40 m/s (144 km/h)
- Field of view: ±60° azimuth, ±40° elevation

### Sending Config to Radar

```bash
# On the Pi:
cd ~/cricket-app
python3 send_config.py profile_3d.cfg
```

This sends each line of the config file to `/dev/ttyUSB0` and waits for "Done" response.

---

## Starting the System

### Current Manual Process

**On the Pi (via SSH or terminal):**

```bash
# 1. Send radar config (if not already running)
cd ~/cricket-app
python3 send_config.py profile_3d.cfg

# 2. Start WebSocket server
cd ~/cricket-app
python3 -m server.websocket_server
```

**On your browser:**

1. Ensure phone/laptop is on same network as Pi
2. Open the Vercel app URL
3. Configure server URL to `ws://raspberrypi.local:5002` (or Pi's IP)
4. App should connect

### Target Automated Process (After WiFi AP setup)

1. Plug in device (power)
2. Device boots, creates "CricketRadar" WiFi network
3. Radar auto-configures
4. Server auto-starts
5. User connects to WiFi, opens app
6. Everything works

---

## Database

### Location

On Pi: `/home/bdrysdale/cricket-app/db/cricket.db`

### Connecting

```bash
sqlite3 /home/bdrysdale/cricket-app/db/cricket.db
```

### Key Tables

```sql
-- View all players
SELECT * FROM players;

-- View all sessions
SELECT * FROM sessions ORDER BY created_at DESC;

-- View deliveries for a session
SELECT * FROM deliveries WHERE session_id = ? ORDER BY ball_number;

-- Session summary
SELECT * FROM session_summaries;
```

---

## Troubleshooting

### WebSocket won't connect

1. Check Pi is running: `ping raspberrypi.local`
2. Check server is running: `ssh bdrysdale@raspberrypi.local "pgrep -f websocket_server"`
3. Check port is open: `nc -zv raspberrypi.local 5002`
4. Check firewall: `sudo ufw status` (should be inactive or allow 5002)

### Radar not outputting data

1. Check USB connected: `ls /dev/ttyUSB*` (should show ttyUSB0 and ttyUSB1)
2. Check radar responds:
   ```python
   import serial
   ser = serial.Serial('/dev/ttyUSB0', 115200, timeout=1)
   ser.write(b'\r\n')
   print(ser.read(100))  # Should show "mmwDemo:/>"
   ```
3. Re-send config: `python3 send_config.py profile_3d.cfg`

### Database errors

1. Check file exists: `ls -la ~/cricket-app/db/cricket.db`
2. Check permissions: `chmod 644 ~/cricket-app/db/cricket.db`
3. Run migrations: `cd ~/cricket-app && python3 -m db.migrate`

---

# PART 3: WAYS OF WORKING

---

## Core Principles

1. **Context is King** - Every agent reads CRICKETRADAR_PLAN.md first. Every agent updates it when done.

2. **Shift Left** - Catch problems at design, not integration. Fix bugs at review, not production.

3. **Proportional Rigour** - Match process weight to task risk. Don't over-engineer trivial changes.

4. **Written Over Verbal** - All decisions, designs, and handoffs documented. Nothing lives only in memory.

5. **Quality is Non-Negotiable** - Reviewer has absolute veto. No exceptions, no "ship it and fix later."

6. **Consistency, Unless Wrong** - Follow existing patterns IF they are good. If a pattern is inefficient, insecure, or poorly designed - fix it, don't copy it. Improving the codebase is always preferred over consistent mediocrity.

7. **Single Source of Truth** - CRICKETRADAR_PLAN.md is always current. If it's not in the doc, it doesn't exist.

---

## Agent Roles

| Role | Responsibility | Authority |
|------|----------------|-----------|
| **Lead** | Owns task end-to-end, coordinates agents, makes scope decisions | Decides task classification, assigns agents |
| **Architect** | System design, contracts, interfaces, cross-component decisions | Approves/blocks designs |
| **Builder** | Writes code, implements solutions | None - must submit to review |
| **Reviewer** | Quality gate for all code and design | **Absolute veto** - can block indefinitely |
| **Tester** | Test strategy, test implementation, validation | Fails task if tests inadequate |
| **Specialist** | Domain expertise (Hardware, Security, UX) | Advisory, but Reviewer enforces |

**Key Rules:**
- Builder can never be Reviewer for their own code
- Reviewer's veto can only be overridden by user (project owner)
- Lead is accountable for task completion, not just delegation

---

## Task Classification

| Class | Criteria | Required Agents | Process |
|-------|----------|-----------------|---------|
| **Trivial** | Config change, typo fix, <10 lines, no logic change | Builder + Reviewer | Build → Review |
| **Small** | Bug fix, minor tweak, 1-3 files, <100 lines | Builder + Reviewer | Build → Review |
| **Medium** | New feature in existing component, clear requirements | Builder + Reviewer + Tester | Scope → Build → Test → Review |
| **Large** | New component, API change, architecture impact | Architect + Builder + Reviewer + Tester | Design → Scope → Test Plan → Build → Review → Integrate |
| **Critical** | Security, auth, data handling, core algorithms, hardware interface | Full team + Security checklist | Design → Security Review → Test Plan → Build → Review → Security Audit → Integrate |

**Classification Decision Tree:**
```
Is it security/auth/data related?
  → YES: Critical

Does it create a new component or change system architecture?
  → YES: Large

Does it add new user-facing functionality?
  → YES: Medium

Does it change logic or behavior?
  → YES: Small

Is it cosmetic, config, or documentation only?
  → YES: Trivial
```

---

## The Process

### Phase 0: INTAKE (All tasks)

Before any work begins:

1. **Read CRICKETRADAR_PLAN.md** - Understand current system state
2. **Classify the task** - Trivial/Small/Medium/Large/Critical
3. **Identify scope** - What files, components, contracts affected
4. **Check dependencies** - What must be true before we start?

**Exit Criteria:** Task classification agreed, scope documented.

---

### Phase 1: DESIGN (Large + Critical only)

Architect produces design containing:

- **Goal:** What are we building and why?
- **Approach:** How will it work?
- **Contracts:** What interfaces/APIs will be created or changed?
- **Components Affected:** Which files/modules will be touched?
- **Risks:** What could go wrong?
- **Acceptance Criteria:** How do we know it's done?

**Exit Criteria:** Reviewer approves design BEFORE any code is written.

---

### Phase 2: TEST STRATEGY (Medium + Large + Critical)

Before implementation, define:

- What functionality must be tested?
- What does success look like?
- What edge cases exist?
- If exploratory: what questions are we answering?

**For exploratory/research work:**
- Tests may come AFTER discovery
- But findings MUST be documented
- And tests MUST be written before task closes

**Exit Criteria:** Test strategy documented, Reviewer acknowledges.

---

### Phase 3: BUILD

Builder implements solution following:

1. **Match the design** (if design phase occurred)
2. **Follow existing patterns IF they are good** - If pattern is bad, improve it
3. **Self-review before submitting** - Builder checks own work first
4. **Include tests** - As defined in test strategy
5. **Update documentation** - Comments, docstrings, CRICKETRADAR_PLAN.md

**Builder's Self-Review Checklist:**
- [ ] Does this match the design/scope?
- [ ] Did I follow existing patterns? If not, is my approach better?
- [ ] Are there any hardcoded values that should be config?
- [ ] Did I handle errors appropriately?
- [ ] Would I understand this code in 6 months?
- [ ] Are tests included and passing?

**Exit Criteria:** Builder certifies self-review complete, submits for review.

---

### Phase 4: REVIEW (All tasks, mandatory)

**Correctness**
- [ ] Does it do what was specified?
- [ ] Are all edge cases handled?
- [ ] Are error conditions handled gracefully?

**Code Quality**
- [ ] Is the code concise? (No unnecessary complexity)
- [ ] Is the code efficient? (No obvious performance issues)
- [ ] Is the code readable? (Clear naming, logical structure)
- [ ] Does it follow existing patterns? **If not, is the deviation an improvement?**
- [ ] If copying an existing pattern, is that pattern actually good? **If not, flag for refactor.**

**Security**
- [ ] No hardcoded credentials or secrets
- [ ] Input validation present where needed
- [ ] No SQL injection, XSS, or command injection vectors
- [ ] Auth checks present for protected operations

**Testing**
- [ ] Tests exist and are meaningful (not just coverage theatre)
- [ ] Tests cover happy path AND error cases
- [ ] Tests would catch regression if code changed

**Documentation**
- [ ] Code comments explain WHY, not just WHAT
- [ ] CRICKETRADAR_PLAN.md updated if system state changed
- [ ] API/contract changes documented

**UX (if user-facing)**
- [ ] Interaction feels intuitive
- [ ] Error messages are helpful
- [ ] Loading/feedback states present

**Embedded/Hardware (if applicable)**
- [ ] Memory usage considered
- [ ] Timing constraints met
- [ ] Error recovery handles hardware failures
- [ ] Serial/IO properly managed

**Review Outcomes:**
- **APPROVED** - Proceed to next phase
- **CHANGES REQUESTED** - Specific, actionable feedback provided. Builder must address ALL items.
- **BLOCKED** - Fundamental flaw. Requires redesign. Return to appropriate phase.

**Review Rules:**
- Feedback must be specific and actionable
- Builder addresses ALL comments before re-review
- No "approval with reservations" - either approved or not

**Exit Criteria:** Reviewer marks APPROVED.

---

### Phase 5: INTEGRATE (Large + Critical)

After approval, verify the whole system still works:

1. **Build succeeds** - No compilation/runtime errors
2. **All tests pass** - Existing AND new
3. **Cross-component check** - Test integration points
4. **Manual verification** - For UI changes, visually confirm

**Exit Criteria:** System demonstrably works end-to-end.

---

### Phase 6: DOCUMENT (All tasks)

Every task ends with:

1. **Update CRICKETRADAR_PLAN.md** if:
   - System architecture changed
   - New component added
   - Status of any feature changed
   - New decisions made
   - New questions discovered

2. **Record learnings** if applicable:
   - What was harder than expected?
   - What would we do differently?

**Exit Criteria:** Documentation current, task can be marked complete.

---

## UI Preservation Rule

> **CRITICAL: The frontend UI (App.tsx, App.css, fieldZones.ts) is COMPLETE and WORKING. Do NOT rewrite or restructure these files.**

The fielding UI, scoring display, wagon wheel, and shot simulator have been carefully built and refined. When integrating new features:

1. **DO NOT** refactor or restructure App.tsx
2. **DO NOT** change how state is managed unless absolutely necessary
3. **DO NOT** rename variables or reorganize code "for clarity"
4. **DO** add new imports at the top
5. **DO** add new components as separate files (e.g., `src/components/`)
6. **DO** add hooks that wrap existing logic without replacing it
7. **DO** add small UI elements (like connection status) without moving existing code

**WebSocket Integration Approach:**
- Create hooks that mirror the existing localStorage-based state management
- The UI calls the same functions, but under the hood they talk to the server
- Add connection status indicator as a small addition, not a rewrite
- ServerConfig modal is a new component, doesn't touch existing UI

**If you need to modify App.tsx:**
1. Make the MINIMUM change necessary
2. Do NOT reorganize surrounding code
3. Do NOT "improve" or "clean up" unrelated sections
4. Test that the fielding UI still works exactly as before

---

## Context Preservation Protocol

**Starting a Task:**
1. Read CRICKETRADAR_PLAN.md completely
2. Read any related code files
3. Understand current state before proposing changes
4. If unclear, ASK before assuming

**During a Task:**
1. Document decisions as you make them
2. If design changes, update design doc
3. If you discover something, write it down immediately

**Ending a Task:**
1. Update CRICKETRADAR_PLAN.md
2. Ensure code comments explain non-obvious decisions
3. Verify another agent could continue your work with no verbal handoff
4. **ALWAYS commit and push to GitHub** - Vercel deploys from GitHub, so changes aren't live until pushed
5. If Pi server code changed, remind user to redeploy to Pi

---

## Escalation Protocol

**If Builder disagrees with Reviewer:**
1. Builder presents reasoning in writing
2. Reviewer responds in writing
3. If still unresolved, escalate to Lead
4. Lead makes final call (documented with rationale)
5. User (project owner) can override anyone

**If requirements are unclear:**
1. Do NOT guess and implement
2. Document the ambiguity
3. Ask user for clarification
4. Update CRICKETRADAR_PLAN.md with answer

---

## The Golden Rule

> **No code is merged without review. No review passes without meeting the checklist. No task is complete without documentation updated.**

---

## Reference Links

- **Codebase:** `/Users/Ben/Documents/cricket-app/`
- **Frontend:** Deployed on Vercel
- **This document:** `/Users/Ben/Documents/cricket-app/CRICKETRADAR_PLAN.md`

---

*Last updated: 2026-03-05 - Boot automation complete (Phase 3 partial), PWA infrastructure done (Phase 5 partial)*
