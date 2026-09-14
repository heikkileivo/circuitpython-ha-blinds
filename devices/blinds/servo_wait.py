"""Waiting on a servo's MOVING flag, with a deadline on every wait. It's
given the reads and a clock, so the host tests run it."""

import asyncio

# Starting values, which the lift_*_deadline_ms and tilt_*_ms settings
# override: how long the lift servo gets to start moving and to stop, and
# the tilt servo to arrive, from the command.
START_DEADLINE_MS = 2000
STOP_DEADLINE_MS = 2000
TILT_DEADLINE_MS = 3000
# How long after a goal write the tilt servo gets to raise MOVING. If it
# doesn't, it was at the goal already.
TILT_RISE_MS = 100


async def until_moving(is_moving, clock, deadline_ms):
    """Wait for a read that the servo is moving. is_moving() returns None
    when the servo didn't reply. Returns whether it moved before
    deadline_ms, in the clock's ms."""
    started = clock()
    while not is_moving():
        if clock() - started >= deadline_ms:
            return False
        await asyncio.sleep(0)
    return True


async def until_still(is_moving, clock, deadline_ms, rise_ms=0):
    """Wait for the servo's move to end: a read says it isn't moving, after
    MOVING has risen or rise_ms has passed. A goal write raises MOVING some
    milliseconds later, so until then a read that it isn't moving means
    nothing yet (#85). is_moving() returns None when the servo didn't reply,
    which never counts as not moving. Returns whether the move ended before
    deadline_ms, in the clock's ms."""
    started = clock()
    risen = False
    while True:
        moving = is_moving()
        elapsed = clock() - started
        if moving:
            risen = True
        elif moving is not None and (risen or elapsed >= rise_ms):
            return True
        if elapsed >= deadline_ms:
            return False
        await asyncio.sleep(0)
