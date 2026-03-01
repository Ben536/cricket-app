# Cricket App State Machine

This document defines all valid state transitions for the Cricket App.
State transitions are LAW - implementations MUST enforce these rules.

## Connection States

```
                    ┌──────────────┐
                    │ disconnected │
                    └──────┬───────┘
                           │ connect()
                           ▼
                    ┌──────────────┐
              ┌────▶│  connecting  │◀────┐
              │     └──────┬───────┘     │
              │            │ onopen      │
              │            ▼             │
    timeout/  │     ┌──────────────┐     │ reconnect()
    error     │     │  connected   │─────┤
              │     └──────┬───────┘     │
              │            │ onclose     │
              │            ▼             │
              │     ┌──────────────┐     │
              └─────│ reconnecting │─────┘
                    └──────────────┘
                           │ max retries exceeded
                           ▼
                    ┌──────────────┐
                    │ disconnected │
                    └──────────────┘
```

### State Definitions

| State | Description |
|-------|-------------|
| `disconnected` | No WebSocket connection. UI should show "Offline" indicator. |
| `connecting` | Attempting to establish connection. UI should show "Connecting..." |
| `connected` | Active WebSocket connection. Normal operation. |
| `reconnecting` | Connection lost, attempting to restore. UI should show "Reconnecting..." |

### Valid Transitions

| From State | To State | Trigger | Actions |
|------------|----------|---------|---------|
| `disconnected` | `connecting` | User action or auto-connect | Open WebSocket |
| `connecting` | `connected` | WebSocket `onopen` | Request session state |
| `connecting` | `disconnected` | Timeout (10s) or error | Clear local state |
| `connected` | `reconnecting` | WebSocket `onclose` | Save local state, start retry timer |
| `reconnecting` | `connecting` | Retry timer (1s, 2s, 4s, 8s backoff) | Open new WebSocket |
| `reconnecting` | `disconnected` | Max retries (5) exceeded | Show error, offer manual reconnect |

### Valid Messages by Connection State

| State | Can Send | Can Receive |
|-------|----------|-------------|
| `disconnected` | None | None |
| `connecting` | None | `connection_status`, `error` |
| `connected` | All client messages | All server messages |
| `reconnecting` | None | None |

---

## Session States

```
                    ┌──────────┐
                    │   none   │
                    └────┬─────┘
                         │ start_session
                         ▼
                    ┌──────────┐
                    │  active  │◀────┐
                    └────┬─────┘     │ undo (if history exists)
                         │           │
                         ├───────────┤ manual_input / shot_result
                         │           │
                         │ end_session
                         ▼
                    ┌──────────┐
                    │  ended   │
                    └────┬─────┘
                         │ start_session (new session)
                         ▼
                    ┌──────────┐
                    │  active  │
                    └──────────┘
```

### State Definitions

| State | Description |
|-------|-------------|
| `none` | No active session. UI shows profile selection, session history. |
| `active` | Session in progress. Accepting deliveries. |
| `ended` | Session completed or dismissed. Show summary, offer new session. |

### Valid Transitions

| From State | To State | Trigger | Actions |
|------------|----------|---------|---------|
| `none` | `active` | `start_session` message | Create session in DB, send `session_state` |
| `active` | `active` | `manual_input` or radar delivery | Record delivery, send `shot_result` |
| `active` | `active` | `undo` (if history) | Remove last delivery, send `session_state` |
| `active` | `ended` | `end_session` message | Mark session complete, send `session_state` |
| `active` | `ended` | Dismissal (caught out, retired) | Mark session complete, send `session_state` |
| `ended` | `active` | `start_session` (same profile) | Create new session |
| `ended` | `none` | User navigates away | Clear session state |

### Valid Messages by Session State

| State | Valid Client Messages | Expected Server Responses |
|-------|----------------------|--------------------------|
| `none` | `create_profile`, `select_profile`, `start_session` | `session_state`, `connection_status`, `error` |
| `active` | `manual_input`, `undo`, `set_field`, `set_difficulty`, `end_session` | `shot_result`, `session_state`, `wagon_wheel_update`, `ball_tracking`, `error` |
| `ended` | `start_session`, `select_profile` | `session_state`, `error` |

---

## Combined State Matrix

The following combinations are valid:

| Connection | Session | Description |
|------------|---------|-------------|
| `disconnected` | `none` | Initial state, app not connected |
| `disconnected` | `active` | Connection lost during session (local state preserved) |
| `disconnected` | `ended` | Connection lost after session end |
| `connecting` | `none` | Establishing connection |
| `connecting` | `active` | Reconnecting during session |
| `connected` | `none` | Connected, no session |
| `connected` | `active` | Normal operation |
| `connected` | `ended` | Session just ended |
| `reconnecting` | `none` | Reconnecting after idle disconnect |
| `reconnecting` | `active` | Reconnecting during session (most common) |
| `reconnecting` | `ended` | Reconnecting after session end |

---

## Message Validation Rules

### Client Message Preconditions

| Message Type | Required Connection State | Required Session State | Additional Preconditions |
|--------------|--------------------------|----------------------|--------------------------|
| `set_field` | `connected` | `active` | At least 1 fielder |
| `set_difficulty` | `connected` | any | Valid difficulty value |
| `select_profile` | `connected` | `none` or `ended` | Profile must exist |
| `create_profile` | `connected` | any | Name 1-100 chars |
| `manual_input` | `connected` | `active` | Valid ball result |
| `start_session` | `connected` | `none` or `ended` | Valid profile_id |
| `end_session` | `connected` | `active` | session_id matches active |
| `undo` | `connected` | `active` | Undo history not empty |

### Server Message Postconditions

| Message Type | State Changes | Client Actions |
|--------------|--------------|----------------|
| `shot_result` | None | Update UI, add to wagon wheel |
| `session_state` | May change session state | Sync all UI state |
| `wagon_wheel_update` | None | Add shot line to visualization |
| `ball_tracking` | None | Update tracking indicator |
| `connection_status` | May change connection state | Update connection indicator |
| `error` | Depends on error code | Display error, may retry |

---

## Error Recovery

### On Connection Loss During Active Session

1. Transition to `reconnecting` state
2. Preserve all local session state
3. Attempt reconnection with exponential backoff
4. On reconnection:
   - Send `session_state` request
   - Server responds with current state
   - If server state differs, show conflict resolution UI
5. If max retries exceeded:
   - Save session locally
   - Transition to `disconnected`
   - Offer manual retry

### On Invalid State Transition Attempt

1. Server rejects message with `error` response
2. Error code: `INVALID_STATE_TRANSITION`
3. Client should log error and sync state
4. Send `session_state` request to resync

### On Version Conflict (Optimistic Locking)

1. Server rejects update with `error` response
2. Error code: `VERSION_CONFLICT`
3. Client must fetch latest state
4. Retry operation with updated version
