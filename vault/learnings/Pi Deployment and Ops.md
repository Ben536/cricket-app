# Learning: Pi Deployment and Ops

## Access
- Pi on the LAN at **192.168.0.191**, hostname `cricketradar`, MAC `b8:27:eb:38:60:d7`.
  As of **2026-09-06** `cricketradar.local` DOES resolve from the Mac
  (`raspberrypi.local` does not — it is not the hostname). Prefer the IP for
  scripts; mDNS is fine interactively.
- **Finding it when it is "missing":** sweep the subnet and look for the Pi
  OUI rather than trusting mDNS —
  `for i in $(seq 1 254); do (ping -c1 -W400 192.168.0.$i >/dev/null 2>&1 &); done; sleep 10; arp -a | grep -i b8:27:eb`.
  No `b8:27:eb` on the subnet means the Pi is genuinely not on that network
  (check for the `CricketRadar` AP instead — see the autohotspot section).
- **The Pi 3B+ has no RTC.** After a cold boot its clock is whatever
  fake-hwclock last saved until NTP corrects it, so early journal lines can
  carry a date months old and `journalctl --list-boots` can look like one
  enormous boot. Do not read that as an uptime figure.
- SSH user `bdrysdale`. **Key-based auth installed** (`ssh-copy-id`) — no password needed now. Passwordless `sudo` works.
- ⚠️ The login password was committed to the public repo (now redacted in working tree). **Still in git history — rotate + scrub.**

## Code deploy = rsync, not git
The Pi's `~/cricket-app` is **not a git repo**; code is pushed with `scripts/deploy_to_pi.sh` (rsync of `server/ db/ engine/ contracts/`, excludes `*.db`). To deploy changes:
```
rsync -az --exclude '__pycache__' --exclude '*.pyc' --exclude '*.db' \
  server/ db/ bdrysdale@192.168.0.191:/home/bdrysdale/cricket-app/<dir>/
```

## UI is served from the Pi
`cricket-ui.service` serves `~/cricket-app/dist/`. Frontend changes deploy by `npm run build` then copying `dist/` to the Pi — **works offline, no Vercel/internet**. (A Vercel copy also exists.)

## systemd services (boot automation)
`cricket-server` (WS :5002), `cricket-ui` (:80), `cricket-radar` (configures IWR6843), `cricket-health` (watchdog). Restart: `sudo systemctl restart cricket-server`.
- ⚠️ Restarting a service can race with `cricket-health`; one restart reported "Job canceled" and the Pi rebooted (recovered in ~35s via boot automation). Verify reachability after restarts.

## Radar enumeration — the USB-serial chip is a Silicon Labs CP2105 (NOT a TI XDS110!)
**Confirmed 2026-06-27:** the board's USB-to-serial bridge is a **Silicon Labs
CP2105 Dual USB to UART** (vendor `0x10C4`), not a TI XDS110. On the Mac it showed
as "CP2105 Dual USB to UART Bridge Controller" with two `/dev/cu.usbserial-*` ports.
- On Linux the **`cp210x`** driver binds it → **`/dev/ttyUSB0` + `/dev/ttyUSB1`**
  (config @115200, data @921600) — exactly what the project expects. This is why
  it's `ttyUSB*` and not `ttyACM*`.
- ⚠️ Do NOT look for "Texas Instruments / vendor 0451" in `lsusb` — that was a
  red herring that cost us time. Look for **`10c4`** / "CP210x" / Silicon Labs.
- If missing: `cricket-radar.service` sits in `activating`, "Waiting for /dev/ttyUSB0".

### Root cause of the 2026-06-27 "radar not detected" saga
Two stacked problems, both now understood:
1. **Charge-only USB cables** — multiple cables delivered power (board LEDs on,
   even browned out the old PSU) but **no data**, so nothing enumerated on the Pi
   OR the Mac. A known-good DATA cable made the CP2105 appear instantly.
2. **Pi undervoltage** (see above) — separate issue, fixed with a proper PSU.
Diagnosis tip: isolate cable-vs-board by plugging into a second host (the Mac) —
a working hub there (it enumerated a card reader) proved the path, so when the
radar finally appeared with the right cable, the board was fine all along.

## ⚡ Undervoltage is the root cause of "radar not detected" (2026-06-27)
`vcgencmd get_throttled` returned **`0xd0005`**: under-voltage NOW (bit0), currently
throttled (bit2), and under-voltage / freq-capped / throttled have all occurred
(bits 16/18/19). The Pi 3B+ power supply is inadequate.
- An undervolted Pi **clamps USB port power**, so the IWR6843 never enumerates
  (no TI `0451` device, no `ttyUSB*`) even though its own lights are on. dmesg
  showed `hwmon1: Undervoltage detected!` repeatedly.
- **Fix:** use an official **5.1V / 2.5A+** Pi supply; power the radar via a
  **powered USB hub** or its own supply (don't draw it from the strained Pi);
  use a real **data** USB cable. Re-check with `vcgencmd get_throttled` → want `0x0`.
- Undervoltage also destabilises the Pi generally — a reliability risk beyond the radar.

## Health monitor reboot loop — FIXED (2026-06-27)
The old `health_monitor.py` rebooted the Pi after 3 mixed radar+server restart
attempts, and the counter only reset on a *fully healthy* check. With the radar
absent that never happened → **reboot every ~90s**. Fixed: radar is non-critical
(degraded only, never reboots; only restarts the radar service if the device is
actually present), server has its own restart budget, reboot is a server-only last
resort. Deployed + `cricket-health` re-enabled; uptime now stable. See
[[Development Roadmap]].

## Radar frame rate (Hz) — set to 20Hz (2026-06-27)
Frame rate is the **5th field of `frameCfg`** (ms period) in `~/profile_cricket.cfg`
(sent by `cricket-radar` at boot): `frameCfg 0 2 32 0 <PERIOD_MS> 1 0`.
`100`=10Hz, `50`=20Hz, `33`=30Hz.

Two hard-won lessons:
1. **Changing the rate requires a HARDWARE power-cycle of the radar.** The IWR6843
   cannot change frame/chirp config on the fly: a `cricket-radar` restart
   (sensorStop→reconfig→sensorStart) reports "success" but the chip stays silent
   (0 bytes), and a **soft `reboot` does NOT fix it** (USB power isn't cut).
   Only unplug/replug (cuts USB power → chip reset) applies a new rate.
   → Procedure: edit `frameCfg`, then **power-cycle the Pi**, then verify.
2. **~20Hz is the ceiling at 921600 baud.** Each frame is ~3KB (100+ points) and
   takes ~33ms to transmit over UART. 30Hz (33ms window) can't fit transmit +
   processing → 0 frames. 20Hz (50ms) fits and was measured at a clean **20.0 Hz**.
   To go higher later: raise the radar's UART baud, or trim points/frame (CFAR).

Verify rate: read `/dev/ttyUSB1` @921600, count magic headers `02 01 04 03 06 05 08 07`
per second.

## Session 2026-09-06: what a healthy-but-radarless Pi looks like

Checked over SSH after the Pi appeared to "switch on and off" on a monitor.
Findings worth keeping:

- **`python3-websockets` on the Pi is 16.0** (apt), and the legacy
  `websockets.server` API our server uses **still works there** — the server
  started, listened and answered a real ping/pong. `requirements.txt` pins
  `<16` on the belief the legacy API ends at 15; that pin is about the dev
  environment and is now known to be conservative. Do not loosen it without
  testing, but do not panic if apt installs 16 on the Pi.
- **`connection rejected (400 Bad Request)` every 30s in `cricket-server`
  logs is the OLD watchdog**, not a client bug: its probe opened a bare TCP
  connection and never completed a handshake (2026-08 review, T1.4). If you
  still see those lines, the Pi is running pre-2026-09 code.
- **`vcgencmd get_throttled` = `0x50000`** means undervoltage and throttling
  *have occurred since boot* but are not happening now (the low nibble is
  clear). `dmesg` showed a 2-second dip 15s into boot — with **no radar
  attached**. On a 3B+ that means the supply is marginal before the radar
  even draws anything. Bits: 0 = under-voltage now, 2 = throttled now,
  16 = under-voltage has occurred, 18 = throttling has occurred.
- **`lsusb` is the fastest radar check.** Vendor `10c4` (Silicon Labs
  CP2105) present = the board is on the bus; absent = no cable/no power/dead
  cable, and `/dev/ttyUSB*` will never appear. `cricket-radar.service` then
  sits in `activating`, counting down its 120s device wait, and retries
  forever — which is expected, not a fault.
- `connection_status.radar_connected` over the WebSocket reports the same
  thing without SSH.

## Frame rate: where the missing frames actually go (measured 2026-09-06)

The radar was configured and streaming, but preflight reported **6.4 Hz**
against a profile that asks for 20. Measured step by step on the Pi, with the
CPU otherwise idle:

| measurement | result |
|---|---|
| Raw bytes off `/dev/ttyUSB1` | **49 KB/s, 20.0 Hz of frame headers** — the radar is fine |
| `TLVParser` throughput, profiled on captured bytes | **646 frames/s** (1.55 ms/frame) — the parser is 30x faster than needed |
| Packets actually found in the byte stream | 17.4 Hz — so **~13% never arrive intact** |
| Of those, structurally valid | **85% accepted, 15% truncated TLV** |
| Rejected for trailing bytes / count mismatch | **zero** — the T0.1 validation is not over-strict |
| Full `RadarSource` path (reader + dispatch + subscriber) | 9.8 Hz |

Conclusions, in order of what they rule out:

1. **Not the radar** and **not the parser.** Both are comfortably fast.
2. **Bytes are lost at the UART/USB layer**, mid-packet, ~15% of frames. The
   symptom is `truncated TLV`, and every such loss also shows as a gap in
   the hardware frame counter. `in_waiting` never exceeded **511 bytes**, so
   there is only ~11 ms of slack at 49 KB/s: any pause longer than that and
   bytes are gone.
3. **The rest is our own pipeline.** A tight read loop keeps 14.8 Hz; going
   through the reader thread, the queue, the dispatch thread and a subscriber
   costs another ~5 Hz to GIL contention.

### The unrate-limited log line that made it worse

`TLVParser._track_frame_number` logged a WARNING for **every** frame gap,
on the reader thread, and journald writes that to the SD card. Each lost
frame therefore bought a synchronous write, which delayed the reader, which
lost more frames. Rate-limiting it (as `_note_drop` already was) took the
observed rate from **6.4 Hz to 9.8 Hz** with no other change. Never log
per-event on the reader thread.

### The cheap win still on the table

`guiMonitor -1 1 1 1 0 0 1` enables `logMagRange` and `noiseProfile`, which
`radar/tlv.py` parses and throws away: **1,024 of ~2,418 bytes per frame,
42% of the traffic, for nothing**. `guiMonitor -1 1 0 0 0 0 1` keeps the
point cloud, side info and stats and drops the rest, taking the stream from
49 KB/s to ~28 KB/s. That is proportionally more slack against the 511-byte
buffer. **Requires a hardware power-cycle of the radar** (unplug/replug USB)
- a service restart reports success and leaves the chip silent.

## radar recorder/streamer own the serial port
Only one process should read `ttyUSB1`. The cricket-server owns it via the recorder/streamer — don't run a second reader concurrently.

## See also
[[Database Migrations and SQLite]] · [[Data Gathering Mode]]
