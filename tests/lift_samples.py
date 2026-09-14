"""Middle's lift servo, sampled about every 10 ms on 2026-09-13 (#78), from
tests/data. The phases, one after another: 3 turns up at duty 300, 3 at 500,
2 at 800, then 2 down at 800, 3 at 500, and 3 at 300 until the down end
sensor. Going up (negative duty), the servo angle counts up.
"""

from pathlib import Path

MEASURED = Path(__file__).resolve().parent / "data" / "lift_wrap_samples_2026-09-13.txt"
MEASURED_EVERY_MS = 10


def measured_phases():
    """The measured samples, as {phase: (duty, [(time in ms, servo angle,
    speed)])}."""
    phases = {}
    for line in MEASURED.read_text().splitlines():
        if line and not line.startswith("#"):
            name, duty, t, angle, speed, _voltage = line.split()
            phases.setdefault(name, (int(duty), []))[1].append((int(t), int(angle), int(speed)))
    return phases


def as_sampled(duty, samples, offset, sample_ms):
    """The firmware's view of the measured samples: one every sample_ms, the
    first at offset, as (time in ms, servo angle, speed, duty)."""
    picked, due = [], offset
    for t, angle, speed in samples:
        if t >= due:
            picked.append((t, angle, speed, duty))
            due = t + sample_ms
    return picked
