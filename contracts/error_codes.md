# Cricket App Error Codes

All error codes with descriptions. These codes are returned in `error` messages.

## Error Response Format

```json
{
  "type": "error",
  "message_id": "uuid-v4",
  "timestamp": "2024-01-15T10:30:00.000Z",
  "in_reply_to": "uuid-of-client-message",
  "payload": {
    "code": "ERROR_CODE",
    "message": "Human-readable description",
    "details": { ... },
    "recoverable": true
  }
}
```

---

## Connection Errors (1xxx)

| Code | Name | Message | Recoverable | Description |
|------|------|---------|-------------|-------------|
| `E1001` | `CONNECTION_TIMEOUT` | "Connection timed out" | Yes | WebSocket connection attempt exceeded timeout |
| `E1002` | `CONNECTION_REFUSED` | "Connection refused by server" | Yes | Server rejected connection (e.g., too many clients) |
| `E1003` | `CONNECTION_LOST` | "Connection to server lost" | Yes | WebSocket closed unexpectedly |
| `E1004` | `PROTOCOL_ERROR` | "WebSocket protocol error" | No | Invalid WebSocket frame or protocol violation |
| `E1005` | `SERVER_UNAVAILABLE` | "Server is unavailable" | Yes | Server is down or unreachable |
| `E1006` | `HEARTBEAT_TIMEOUT` | "Connection heartbeat timed out" | Yes | No heartbeat response within expected time |

---

## Authentication Errors (2xxx)

| Code | Name | Message | Recoverable | Description |
|------|------|---------|-------------|-------------|
| `E2001` | `AUTH_REQUIRED` | "Authentication required" | No | Request requires authentication |
| `E2002` | `AUTH_INVALID` | "Invalid authentication token" | No | Token is malformed or invalid |
| `E2003` | `AUTH_EXPIRED` | "Authentication token expired" | Yes | Token has expired, re-authenticate |
| `E2004` | `AUTH_INSUFFICIENT` | "Insufficient permissions" | No | User lacks required permissions |

---

## Validation Errors (3xxx)

| Code | Name | Message | Recoverable | Description |
|------|------|---------|-------------|-------------|
| `E3001` | `INVALID_MESSAGE_FORMAT` | "Invalid message format" | No | Message does not conform to schema |
| `E3002` | `INVALID_MESSAGE_TYPE` | "Unknown message type" | No | Unrecognized message type |
| `E3003` | `MISSING_REQUIRED_FIELD` | "Missing required field: {field}" | No | Required field not present |
| `E3004` | `INVALID_FIELD_VALUE` | "Invalid value for field: {field}" | No | Field value fails validation |
| `E3005` | `FIELD_OUT_OF_RANGE` | "Field {field} out of valid range" | No | Numeric field exceeds bounds |
| `E3006` | `INVALID_UUID` | "Invalid UUID format" | No | UUID field is malformed |
| `E3007` | `INVALID_TIMESTAMP` | "Invalid timestamp format" | No | Timestamp is not valid ISO 8601 |
| `E3008` | `PAYLOAD_TOO_LARGE` | "Message payload exceeds maximum size" | No | Payload exceeds 64KB limit |

---

## State Errors (4xxx)

| Code | Name | Message | Recoverable | Description |
|------|------|---------|-------------|-------------|
| `E4001` | `INVALID_STATE_TRANSITION` | "Invalid state transition: {from} -> {to}" | No | Attempted invalid state change |
| `E4002` | `SESSION_NOT_ACTIVE` | "No active session" | No | Operation requires active session |
| `E4003` | `SESSION_ALREADY_ACTIVE` | "Session already active" | No | Cannot start session when one exists |
| `E4004` | `SESSION_NOT_FOUND` | "Session not found: {id}" | No | Referenced session does not exist |
| `E4005` | `PROFILE_NOT_FOUND` | "Profile not found: {id}" | No | Referenced profile does not exist |
| `E4006` | `UNDO_HISTORY_EMPTY` | "No actions to undo" | No | Undo requested but history is empty |
| `E4007` | `SESSION_ENDED` | "Session has already ended" | No | Cannot modify an ended session |

---

## Database Errors (5xxx)

| Code | Name | Message | Recoverable | Description |
|------|------|---------|-------------|-------------|
| `E5001` | `DATABASE_ERROR` | "Database operation failed" | Yes | Generic database error |
| `E5002` | `VERSION_CONFLICT` | "Version conflict, please refresh" | Yes | Optimistic locking failure |
| `E5003` | `CONSTRAINT_VIOLATION` | "Database constraint violated" | No | Foreign key or unique constraint |
| `E5004` | `RECORD_NOT_FOUND` | "Record not found" | No | Requested record does not exist |
| `E5005` | `DATABASE_LOCKED` | "Database is locked" | Yes | SQLite database is locked |
| `E5006` | `STORAGE_FULL` | "Storage is full" | No | Disk space exhausted |

---

## Radar Errors (6xxx)

| Code | Name | Message | Recoverable | Description |
|------|------|---------|-------------|-------------|
| `E6001` | `RADAR_NOT_CONNECTED` | "Radar device not connected" | Yes | No radar hardware detected |
| `E6002` | `RADAR_TIMEOUT` | "Radar response timed out" | Yes | Radar did not respond in time |
| `E6003` | `RADAR_CALIBRATION` | "Radar requires calibration" | Yes | Radar needs recalibration |
| `E6004` | `RADAR_TRACKING_FAILED` | "Failed to track ball" | Yes | Could not establish tracking lock |
| `E6005` | `RADAR_LOW_CONFIDENCE` | "Low tracking confidence" | Yes | Detection confidence below threshold |
| `E6006` | `RADAR_HARDWARE_ERROR` | "Radar hardware error" | No | Hardware malfunction detected |

### Recording State Errors (E61xx)

Recording control (start/stop/annotate) uses a dedicated sub-block so it can
never collide with the hardware codes above.

| Code | Name | Message | Recoverable | Description |
|------|------|---------|-------------|-------------|
| `E6101` | `ALREADY_RECORDING` | "Already recording" | Yes | A recording session is already active |
| `E6102` | `RECORDING_START_FAILED` | "Failed to start recording" | Yes | Recorder could not start (see message) |
| `E6103` | `NOT_RECORDING` | "Not currently recording" | Yes | stop/annotate sent with no active recording |
| `E6104` | `RECORDING_NO_SESSION` | "Recording stop returned no session" | Yes | Internal recorder state error |
| `E6105` | `RECORDING_STOP_FAILED` | "Failed to stop recording" | Yes | Recorder could not finalize (see message) |
| `E6106` | `RECORDING_LIST_FAILED` | "Failed to list recordings" | Yes | Could not enumerate `recordings/` (see message) |
| `E6107` | `RECORDING_READ_FAILED` | "Failed to read recording" | Yes | Could not read a recording's labels (see message). A path outside `recordings/` returns `E3004` instead |

---

## Game Engine Errors (7xxx)

| Code | Name | Message | Recoverable | Description |
|------|------|---------|-------------|-------------|
| `E7001` | `SIMULATION_FAILED` | "Shot simulation failed" | Yes | Game engine simulation error |
| `E7002` | `INVALID_FIELD_CONFIG` | "Invalid field configuration" | No | Field config fails validation |
| `E7003` | `NO_FIELDERS` | "At least one fielder required" | No | Field config has no fielders |
| `E7004` | `INVALID_TRAJECTORY` | "Invalid trajectory parameters" | No | Trajectory calculation failed |
| `E7005` | `BOUNDARY_OUT_OF_RANGE` | "Boundary distance out of range" | No | Boundary must be 50-100m |

---

## Rate Limiting Errors (8xxx)

| Code | Name | Message | Recoverable | Description |
|------|------|---------|-------------|-------------|
| `E8001` | `RATE_LIMITED` | "Too many requests" | Yes | Request rate exceeded |
| `E8002` | `MESSAGE_THROTTLED` | "Message rate exceeded" | Yes | WebSocket message rate too high |

---

## Error Handling Guidelines

### For Recoverable Errors

1. Display user-friendly message
2. Log full error details
3. Offer retry option where appropriate
4. For connection errors, use exponential backoff

### For Non-Recoverable Errors

1. Display error message
2. Log full error details
3. Clear invalid state
4. Guide user to valid state (e.g., return to home)

### Client-Side Error Handling

```typescript
function handleError(error: ErrorPayload) {
  // Log for debugging
  console.error(`[${error.code}] ${error.message}`, error.details);

  // Determine severity
  const isConnection = error.code.startsWith('E1');
  const isValidation = error.code.startsWith('E3');
  const isState = error.code.startsWith('E4');

  if (error.recoverable) {
    // Show toast notification
    showToast(error.message, 'warning');

    if (isConnection) {
      // Trigger reconnection
      initiateReconnect();
    }
  } else {
    // Show modal error
    showErrorModal(error.message);

    if (isState) {
      // Resync state
      requestSessionState();
    }
  }
}
```

### Server-Side Error Generation

```python
def create_error(code: str, details: dict = None) -> ErrorMessage:
    """Create a standardized error message."""
    errors = {
        'E4002': ('SESSION_NOT_ACTIVE', 'No active session', False),
        'E5002': ('VERSION_CONFLICT', 'Version conflict, please refresh', True),
        # ... etc
    }

    name, message, recoverable = errors.get(code, ('UNKNOWN', 'Unknown error', False))

    return ErrorMessage(
        message_id=generate_message_id(),
        timestamp=create_timestamp(),
        payload=ErrorPayload(
            code=code,
            message=message,
            details=details,
            recoverable=recoverable
        )
    )
```
