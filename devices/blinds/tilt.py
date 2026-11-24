"""The tilt HA is told, from the tilt servo's servo angle: the tilt a blind
starts from at boot. The conversion is pure, and the read only needs a
Reader, so the host tests run both."""

from packet import Address
from servo_bus import TILT_ID
from servo_health import ATTEMPTS, first_reply

# The tilt a blind starts from if its tilt servo doesn't reply at boot.
DEFAULT_TILT = 50

# An STS3215 tilt servo has the tilt's 50 at its middle, which its midpoint
# calibration (torque enable 128) puts at 2048, and STS_STEPS_PER_TILT steps
# per tilt percent either way: 40 puts 0 and 100 at 48 and 4048, inside the
# 0-4095 of one turn, about 80 degrees of slats each way on Middle (#141).
STS_MIDDLE = 2048
STS_STEPS_PER_TILT = 40


# The time a tilt move may take, on an STS, besides the full turn it can move
# at the tilt speed: the acceleration ramps, and the dead band settling.
STS_TILT_MARGIN_MS = 1500


def sts_deadline_ms(speed):
    """The deadline for an STS tilt move at speed steps/s: the time a full
    turn takes, which no move exceeds, and a margin. The SCS's 3 s deadline
    would cut a 2,000-step move at 600 steps/s short, which turns the torque
    off mid-move and leaves the cover stopped."""
    return 4096 * 1000 // speed + STS_TILT_MARGIN_MS


def read_at_boot(reader, scale, sts_steps=STS_STEPS_PER_TILT):
    """The tilt the tilt servo is at, read once the boot re-init has
    stopped it. The read is tried up to ATTEMPTS times, like the servo
    health reads. With no reply, it's DEFAULT_TILT."""
    tilt = from_servo_angle(
        first_reply(lambda: reader.read_2_bytes(TILT_ID, Address.PRESENT_POSITION_L)), scale,
        reader.series(TILT_ID), sts_steps)
    if tilt is None:
        print(f"The tilt servo didn't reply to {ATTEMPTS} reads of its servo angle, "
              + f"starting the tilt at {DEFAULT_TILT}.")
        return DEFAULT_TILT
    return tilt


def to_servo_angle(tilt, scale, series="scs", sts_steps=STS_STEPS_PER_TILT):
    """The servo angle to drive the tilt servo to for a tilt: the tilt times
    scale on an SCS, and around STS_MIDDLE on an STS, clamped to one turn."""
    if series == "sts":
        return max(0, min(4095, STS_MIDDLE + (tilt - 50) * sts_steps))
    return int(tilt * scale)


def from_servo_angle(angle, scale, series="scs", sts_steps=STS_STEPS_PER_TILT):
    """The tilt for the tilt servo's servo angle, which the tilt servo is
    driven to as the tilt times scale (an STS: around STS_MIDDLE), rounded to
    the nearest whole tilt and clamped to 0-100, or None if the read got no
    reply."""
    if angle is None:
        return None
    if series == "sts":
        return max(0, min(100, int(50 + (angle - STS_MIDDLE) / sts_steps + 0.5)))
    # The angle is never negative, so this rounds halves up, and the tilt
    # never falls below 0. round() would round halves to even.
    return min(100, int(angle / scale + 0.5))
