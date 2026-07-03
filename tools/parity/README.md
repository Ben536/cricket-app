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

1,154 shots sweep angle quadrants (incl. wraparound), speeds 0-200,
elevations 0-90, boundary edges, all difficulties and every fielding path.
Discrete fields (outcome/runs/flags/fielder/seed) must match exactly;
floats to 1e-6 (cross-language libm noise).

Both engines share engine/engine_params.json (constants) and mulberry32
(engine/prng.py = the TS twin in gameEngine.ts), so identical seeds take
identical stochastic branches. If you change engine behaviour: change BOTH
engines, then run this suite. If you tune a constant: edit the params file
only - the code never needs touching.
