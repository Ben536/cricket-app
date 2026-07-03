"""
mulberry32 - the shared PRNG for both game engines.

The TypeScript engine (src/gameEngine.ts) implements the SAME generator, so a
simulation seeded identically produces identical outcomes in the browser and
on the Pi. This is what makes cross-engine golden tests and shot replay
possible: without a shared seedable PRNG, stochastic outcomes (catch rolls,
misfields) could never be compared or reproduced.

Reference: https://gist.github.com/tommyettinger/46a874533244883189143505d203312c
The Python port emulates JavaScript's 32-bit integer semantics (Math.imul,
unsigned right shift) exactly - see test in tools/parity/.
"""

from __future__ import annotations

from typing import Callable

_MASK32 = 0xFFFFFFFF


def _imul(a: int, b: int) -> int:
    """JavaScript Math.imul: 32-bit signed integer multiplication."""
    result = (a * b) & _MASK32
    return result - 0x100000000 if result >= 0x80000000 else result


def _to_uint32(x: int) -> int:
    return x & _MASK32


def mulberry32(seed: int) -> Callable[[], float]:
    """Return a function yielding floats in [0, 1), identical to the JS twin."""
    state = _to_uint32(seed)

    def rand() -> float:
        nonlocal state
        state = _to_uint32(state + 0x6D2B79F5)
        t = state
        t = _to_uint32(_imul(t ^ (t >> 15), t | 1))
        t = _to_uint32(t ^ (t + _imul(t ^ (t >> 7), t | 61)))
        return _to_uint32(t ^ (t >> 14)) / 4294967296.0

    return rand
