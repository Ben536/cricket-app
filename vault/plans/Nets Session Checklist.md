# Nets Session Checklist

The trip exists to answer three questions the code cannot. Everything else is
a bonus. Print this or keep it open on a phone.

Previous trips produced **19 mock recordings and 1 real one** - the radar was
not detected and the frames were fabricated. Step 2 below is the whole
defence against repeating that.

---

## What the trip must produce

| # | Question | What answers it | Without it |
|---|---|---|---|
| 1 | Does `extendedMaxVelocity` engage at full ball speed? | A **bowling** capture of ~20 balls, no batter | Every shot may alias; the radar approach may not work at this profile at all |
| 2 | How is the sensor oriented? | A **both** capture, 30+ balls, wagon-wheel tap on each, **directions spread across the field** | No field-frame angles: the detector cannot feed the engine |
| 3 | What does a bat look like vs a ball? | A **racket** or **racket_foil** capture, 20 swings, no ball | Bat swings will be mistaken for balls |

Plus one number: **the mount height, with a tape measure.** And a photo of
the mount from the side.

---

## Before you leave the house

```bash
cd ~/cricket-app
npm run check                      # all 10 gates green
./scripts/deploy_to_pi.sh          # gates run BEFORE anything lands on the Pi
```

Pack: the **data** USB cable (a charge-only cable enumerates nothing - this
cost a whole session once), the powered hub, the 5.1V/2.5A+ Pi supply, tape
measure, foil, tennis racket.

---

## 1. At the nets: power up, then pre-flight

```bash
ssh bdrysdale@cricketradar.local            # or the AP: 10.42.0.1
cd ~/cricket-app
python3 scripts/preflight.py --hours 2
```

It checks: both USB ports, all four services, **undervoltage**, disk, the
database, and then samples the radar live. It refuses to say READY on mock
data.

**Bowl a few balls during the 10-second sample.** That is what turns the
`extendedMaxVelocity` verdict from "ACTIVE (unexercised)" into "CONFIRMED".

If it says NOT READY, fix that first - recording anyway produces another
worthless file.

## 2. Verify `mock: false` at the START of every capture

The Data and Rec screens show a red **RADAR NOT DETECTED** banner when the
radar is absent. If you see it: stop, replug, re-run pre-flight. Never keep
recording through it.

## 3. The captures, in this order

**a. bowling** (~20 balls, no batter, ~5 min)
The pure ball signature and the velocity range. Bowl at your normal pace and
include a few as fast as you can.

**b. racket** (~20 swings, no ball, ~3 min)
Shadow swings under the sensor. This is the dominant clutter source.

**c. both** (30+ balls, ~15 min) - **the important one**
Bowl and hit. **Tap the wagon wheel for every single ball**, at the spot the
ball actually went. Two rules:

- **Spread the directions.** Straight, cover, midwicket, square both sides,
  fine. All straight drives cannot fit a mount rotation - the fit needs
  spread more than it needs volume.
- **Tap even for a miss or an edge** if you can say where it went; if you
  cannot, do not tap.

Say the direction out loud as you tap - it keeps the taps honest.

**d. Optional: foil_ball** - a foil-wrapped ball rolled/thrown along a known
line, if there is time. It gives a high-contrast target.

## 4. Measure the mount

- Height from the **ground to the sensor face**, in metres, to the nearest
  5 cm.
- Note which way the board faces relative to the pitch (roughly: is the
  cable end toward the bowler, the keeper, off or leg?).
- Photo from the side and from underneath.

## 5. Before you pack up

```bash
python3 scripts/preflight.py --file recordings/both/<the file you just made>.jsonl
python3 tools/replay_jsonl.py recordings/both/<file>.jsonl
```

The second one says how many balls were detected against how many you
tapped. If recall is 0%, something is wrong and it is worth another capture
while you are still there.

---

## Afterwards, back at the laptop

```bash
# recordings/ is gitignored and lives only on the Pi - copy it off FIRST
rsync -avz bdrysdale@cricketradar.local:~/cricket-app/recordings/ ~/cricket-app/recordings/

# fit the mount from the taps
python3 tools/replay_jsonl.py recordings/both/<file>.jsonl --fit-yaw
```

That prints `yaw_deg`, `mirror` and an RMS error. Put those, plus the
measured height, into `radar/mount.json` and set `"calibrated": true`.
An RMS under about 10 degrees is a good fit; much more means the taps and the
detections are not lining up and the detector needs tuning first.

Then write up the session in `vault/sessions/YYYY-MM-DD.md`:

```
# Net session YYYY-MM-DD
- Setup: mount height, which way it faces, who bowled/batted, ball type
- Pre-flight: the extendedMaxVelocity verdict, frame rate, points/frame
- Recordings: <files> (type, duration, #balls, #taps)
- Replay: recall / precision, speed sanity (does a good drive read ~100 km/h?)
- Fit: yaw, mirror, RMS
- Observations: what looked wrong, what surprised you
- Next: what to change in the detector
```

---

## What to hand the next review

1. The `.jsonl` files (they are the only real data this project has).
2. The pre-flight output from the session.
3. The mount height and the fit numbers.
4. The session note - especially anything that felt wrong at the time.

## What is already known (do not re-derive)

- The profile computes to v_max_base **13.0 m/s** and v_max **39.0 m/s**
  (140 km/h, not the 145 the .cfg comment claims).
- The July 2026 recording shows 88% of its points at **2x the base limit** -
  the static-misassignment artefact, which means extended mode **was**
  engaging that day. The open part is whether it holds at real ball speeds.
- Changing the frame rate or chirp config needs a **hardware power-cycle**.
  A service restart reports success and leaves the chip silent.
- ~20 Hz is the ceiling at 921600 baud.
