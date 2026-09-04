"""
Read the radar's own limits out of its configuration file.

The detector used to treat doppler at face value with a magic
`max_plausible_speed_kmh = 250` unrelated to anything the sensor can
measure, and NO code read config/profile_cricket.cfg: editing profileCfg,
chirpCfg or extendedMaxVelocity silently changed the unambiguous velocity
with zero effect on detection. The one real recording shows exactly the
artefact that predicts - a static-clutter doppler cluster at 25.93 m/s, which
is 2 x 12.97 m/s, the textbook mis-assignment of a target by one ambiguity
interval when extendedMaxVelocity is engaged.

Formulas (TI mmWave SDK, TDM-MIMO):
    chirp_period  = (idleTime + rampEndTime) * numTx        [s]
    lambda        = c / f_centre                            [m]
    v_max_base    = lambda / (4 * chirp_period)             [m/s] unambiguous
    v_max         = v_max_base * numTx  if extendedMaxVelocity else v_max_base
    v_res         = 2 * v_max_base / numLoops               [m/s]
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

C_M_S = 299_792_458.0

DEFAULT_PROFILE_PATH = Path(__file__).resolve().parent.parent / "config" / "profile_cricket.cfg"


@dataclass(frozen=True)
class RadarProfile:
    start_freq_ghz: float
    idle_time_us: float
    ramp_end_time_us: float
    freq_slope_mhz_us: float
    num_adc_samples: int
    num_tx: int                 # chirps per loop = TX antennas in the TDM loop
    num_loops: int
    frame_period_ms: float
    extended_max_velocity: bool
    range_min_m: Optional[float] = None
    range_max_m: Optional[float] = None

    @property
    def centre_freq_ghz(self) -> float:
        # Sweep runs from start_freq for ramp_end_time at freq_slope
        return self.start_freq_ghz + self.freq_slope_mhz_us * self.ramp_end_time_us / 2000.0

    @property
    def wavelength_m(self) -> float:
        return C_M_S / (self.centre_freq_ghz * 1e9)

    @property
    def chirp_period_s(self) -> float:
        return (self.idle_time_us + self.ramp_end_time_us) * 1e-6 * self.num_tx

    @property
    def v_max_base_ms(self) -> float:
        """Unambiguous radial velocity WITHOUT extendedMaxVelocity."""
        return self.wavelength_m / (4.0 * self.chirp_period_s)

    @property
    def v_max_ms(self) -> float:
        """Unambiguous radial velocity the profile actually delivers."""
        return self.v_max_base_ms * (self.num_tx if self.extended_max_velocity else 1)

    @property
    def v_res_ms(self) -> float:
        return 2.0 * self.v_max_base_ms / self.num_loops

    @property
    def frame_rate_hz(self) -> float:
        return 1000.0 / self.frame_period_ms

    def summary(self) -> str:
        return (
            f"f={self.centre_freq_ghz:.2f}GHz lambda={self.wavelength_m * 1000:.2f}mm "
            f"Tc={self.chirp_period_s * 1e6:.0f}us numTx={self.num_tx} loops={self.num_loops} "
            f"frame={self.frame_period_ms:.0f}ms ({self.frame_rate_hz:.0f}Hz) "
            f"v_max_base={self.v_max_base_ms:.2f}m/s "
            f"v_max={self.v_max_ms:.2f}m/s ({self.v_max_ms * 3.6:.0f}km/h, "
            f"extendedMaxVelocity={'on' if self.extended_max_velocity else 'off'}) "
            f"v_res={self.v_res_ms:.2f}m/s"
        )


def parse_profile(text: str) -> RadarProfile:
    """Parse the TI CLI config format (one command per line, % comments)."""
    profile = None
    chirp_ids: list[int] = []
    frame = None
    extended = False
    range_min = range_max = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("%"):
            continue
        parts = line.split()
        cmd, args = parts[0], parts[1:]
        try:
            if cmd == "profileCfg":
                profile = args
            elif cmd == "chirpCfg":
                chirp_ids.append(int(args[0]))
            elif cmd == "frameCfg":
                frame = args
            elif cmd == "extendedMaxVelocity":
                extended = int(args[1]) == 1
            elif cmd == "cfarFovCfg" and int(args[1]) == 0:  # 0 = range FOV
                range_min, range_max = float(args[2]), float(args[3])
        except (IndexError, ValueError) as e:
            raise ValueError(f"Cannot parse '{line}': {e}") from e

    if profile is None or frame is None:
        raise ValueError("profileCfg and frameCfg are both required")

    # profileCfg <id> <startFreq> <idleTime> <adcStartTime> <rampEndTime>
    #            <txOutPower> <txPhaseShifter> <freqSlopeConst> <txStartTime>
    #            <numAdcSamples> <digOutSampleRate> <hpf1> <hpf2> <rxGain>
    start_freq = float(profile[1])
    idle = float(profile[2])
    ramp_end = float(profile[4])
    slope = float(profile[7])
    adc_samples = int(profile[9])

    # frameCfg <chirpStartIdx> <chirpEndIdx> <numLoops> <numFrames> <periodMs> <trigger> <delay>
    chirp_start, chirp_end = int(frame[0]), int(frame[1])
    num_tx = chirp_end - chirp_start + 1
    num_loops = int(frame[2])
    period_ms = float(frame[4])

    if num_tx < 1 or num_loops < 1 or period_ms <= 0:
        raise ValueError("frameCfg values out of range")
    if start_freq <= 0 or (idle + ramp_end) <= 0:
        raise ValueError("profileCfg start frequency and chirp time must be positive")
    if chirp_ids and (chirp_end - chirp_start + 1) > len(chirp_ids):
        raise ValueError("frameCfg references more chirps than chirpCfg defines")

    return RadarProfile(
        start_freq_ghz=start_freq,
        idle_time_us=idle,
        ramp_end_time_us=ramp_end,
        freq_slope_mhz_us=slope,
        num_adc_samples=adc_samples,
        num_tx=num_tx,
        num_loops=num_loops,
        frame_period_ms=period_ms,
        extended_max_velocity=extended,
        range_min_m=range_min,
        range_max_m=range_max,
    )


def load_profile(path: Path = DEFAULT_PROFILE_PATH) -> RadarProfile:
    return parse_profile(Path(path).read_text())
