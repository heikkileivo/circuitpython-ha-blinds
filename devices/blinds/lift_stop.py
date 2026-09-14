"""The lift's stop sequence, which every move ends with, and so does the boot
re-init (#23, #52). See braking in CONTEXT.md.

It stops the lift with duty 0, then leaves it braking: torque switch 2, or
torque 1 if the servo doesn't accept 2, since 1 with duty 0 brakes too. A
duty 0 that can't be confirmed leaves it limp instead, with torque 0, the one
write known to cut the drive, and main() fails, so the restart loop takes
over. Torque 2 is never the fallback, because it's unknown whether it
overrides a non-zero duty.

next_step() is pure: the last write's outcome in, the next write out, so the
host tests run every branch. servo_health carries the writes out."""

# What a write's read-back showed, after its retries.
CONFIRMED = "confirmed"
REFUSED = "refused"         # The servo answered with another value.
NO_REPLY = "no reply"

# The sequence's writes.
DUTY_0 = "duty 0"
BRAKE = "torque 2"
FALLBACK_BRAKE = "torque 1"     # With duty 0, for a servo that refuses 2.
LIMP = "torque 0"

# How the sequence ends.
BRAKED = "braked"
BRAKE_UNCONFIRMED = "stopped, brake unconfirmed"
STOP_UNCONFIRMED = "stop unconfirmed"
ENDS = (BRAKED, BRAKE_UNCONFIRMED, STOP_UNCONFIRMED)


def next_step(last=None, outcome=None):
    """The stop sequence's next write, after the write last had outcome, or
    how the sequence ended. With no write yet, it's duty 0."""
    if last is None:
        return DUTY_0
    if last == DUTY_0:
        return BRAKE if outcome == CONFIRMED else LIMP
    if last == LIMP:
        return STOP_UNCONFIRMED
    if last == BRAKE:
        if outcome == CONFIRMED:
            return BRAKED
        # Only a refusal falls back. With no reply, the motor has stopped
        # anyway, as duty 0 is confirmed.
        return FALLBACK_BRAKE if outcome == REFUSED else BRAKE_UNCONFIRMED
    # The fallback brake.
    return BRAKED if outcome == CONFIRMED else BRAKE_UNCONFIRMED


def stopped(end):
    """Whether a sequence that ended so has stopped the lift: its duty 0 is
    confirmed, braked or not. If not, main() fails."""
    return end != STOP_UNCONFIRMED
