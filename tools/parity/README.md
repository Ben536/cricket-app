# Cross-engine golden parity suite

The app runs the Python engine when connected to the Pi and the TypeScript
engine when offline - the same shot MUST produce the same result in both.
This suite verifies that:

```
python3 tools/parity/gen_shots.py   # regenerate the canonical shot set (committed)
python3 tools/parity/run_py.py      # run all shots through engine/game_engine.py
npx tsx tools/parity/run_ts.ts      # run all shots through src/gameEngine.ts
python3 tools/parity/compare.py     # diff - exit 1 on any divergence
```

2,274 shots sweep angle quadrants (incl. wraparound), speeds 0-200,
elevations 0-90, boundary edges, all difficulties and every fielding path.
Discrete fields (outcome/runs/flags/fielder/seed) must match exactly;
floats to 1e-6 (cross-language libm noise).

`shots[-402:-2]` are a deterministic sweep of the steep band (speed 110-200,
elevation 62-89.5); the final two are adversarial inputs. A fixed angle grid
samples that band far too sparsely to be a reliable guard - 16 fixed rays
rarely pass through a fielder's reachable zone - and it is where the engines
have actually diverged before. Measured against the engine bug it was added
for: the fixed grid alone caught it in 1 shot, the sweep in 10, and the grid
as it stood before the band was widened caught it in **none**.

That sweep detects via `end_x`/`end_y` only - the discrete fields agree on
those shots. Both fields are therefore load-bearing in `compare.py`'s `FLOAT`
list, and loosening `TOL` by ~3 orders of magnitude would collapse the guard
back to a single shot (current margins are ~4,500-6,900x tolerance).

`shots.json` is generated and committed: re-run `gen_shots.py` and commit the
result after any change to the generator. CI fails if the two disagree, and
`compare.py` refuses results whose `shots_sha` does not match the current
`shots.json`, so a stale `results_*.json` can no longer be graded as a pass.

Both engines share engine/engine_params.json (constants) and mulberry32
(engine/prng.py = the TS twin in gameEngine.ts), so identical seeds take
identical stochastic branches. If you change engine behaviour: change BOTH
engines, then run this suite. If you tune a constant: edit the params file
only - the code never needs touching.
