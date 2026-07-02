# Learning: Pi Deployment and Ops

## Access
- Pi on the LAN at **192.168.0.191** (mDNS `raspberrypi.local` / `cricketradar.local` was NOT resolving — use the IP). Hostname is `cricketradar`. MAC `b8:27:eb:…` (Raspberry Pi OUI).
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

## radar recorder/streamer own the serial port
Only one process should read `ttyUSB1`. The cricket-server owns it via the recorder/streamer — don't run a second reader concurrently.

## See also
[[Database Migrations and SQLite]] · [[Data Gathering Mode]]
