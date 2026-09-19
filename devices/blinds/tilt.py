"""The tilt HA is told, from the tilt servo's servo angle: the tilt a blind
starts from at boot. The conversion is pure, and the read only needs a
Reader, so the host tests run both."""

from packet import Address
from servo_bus import TILT_ID
from servo_health import ATTEMPTS, first_reply

# The tilt a blind starts from if its tilt servo doesn't reply at boot.
DEFAULT_TILT = 50


def read_at_boot(reader, scale):
    """The tilt the tilt servo is at, read once the boot re-init has
    stopped it. The read is tried up to ATTEMPTS times, like the servo
    health reads. With no reply, it's DEFAULT_TILT."""
    tilt = from_servo_angle(
        first_reply(lambda: reader.read_2_bytes(TILT_ID, Address.PRESENT_POSITION_L)), scale)
    if tilt is None:
        print(f"The tilt servo didn't reply to {ATTEMPTS} reads of its servo angle, "
              + f"starting the tilt at {DEFAULT_TILT}.")
        return DEFAULT_TILT
    return tilt


def from_servo_angle(angle, scale):
    """The tilt for the tilt servo's servo angle, which the tilt servo is
    driven to as the tilt times scale, rounded to the nearest whole tilt and
    clamped to 0-100, or None if the read got no reply."""
    if angle is None:
        return None
    # The angle is never negative, so this rounds halves up, and the tilt
    # never falls below 0. round() would round halves to even.
    return min(100, int(angle / scale + 0.5))
