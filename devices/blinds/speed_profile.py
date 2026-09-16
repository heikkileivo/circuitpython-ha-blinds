"""The lift's speed profile (#10, #57): what duty a drive runs at, from the
revolutions it has turned and the revolutions left to its end. Pure, so the
host tests run it.

It replaces the two-step speed the moves drove at before, full speed until a
fixed distance from the end and then the approach speed:

  - a soft start, smoothstepping from SOFT_START_SPEED up to cruise over the
    first SOFT_START_REVS, which cuts the current spike on the shared supply
  - cruise
  - a smoothstep from cruise down to the approach speed over the last
    slowdown_revs, so the blind arrives at the approach speed from any
    starting point, mid-height ones included
  - the approach speed from there on, until the end sensor stops the drive:
    the travel is an estimate until it's learned, so the end comes a little
    before or after it

Both directions work the same way: the duties are negative up. Where the two
ramps overlap, on a drive that starts within slowdown_revs of the end, the
slower of the two wins, so the speed never runs ahead of the distance left.
With the travel unknown, after an interrupted move or with a blank NVM, the
drive runs at the approach speed all the way, until an end sensor re-anchors
the travel (#50).
"""

import math

# Starting values, which the settings.toml keys of the same names override,
# tuned at stage 9's gate (#58).
SOFT_START_SPEED = 300
SOFT_START_REVS = 1.0
# The slowest the servo drives without stalling.
MIN_SPEED = 200
# How far from the end the slowdown starts, in revolutions: the
# open_approach_revs and close_approach_revs of settings.toml.
SLOWDOWN_REVS = 3.0
# The profile's speed updates go out at most this often, in ms.
UPDATE_MS = 100


def _smoothstep(t):
    """A smooth 0-to-1 ramp over t, clamped outside 0-1."""
    if t <= 0:
        return 0.0
    if t >= 1:
        return 1.0
    return t * t * (3 - 2 * t)


class Profile:
    """The speed profile of one drive of the lift. cruise and approach are
    its duties, both negative for a drive up; the profile follows cruise's
    direction."""

    def __init__(self, cruise, approach, slowdown_revs=SLOWDOWN_REVS,
                 soft_start_revs=SOFT_START_REVS, soft_start_speed=SOFT_START_SPEED,
                 min_speed=MIN_SPEED):
        self._up = cruise < 0
        self._cruise = abs(cruise)
        self._approach = abs(approach)
        self._slowdown_revs = slowdown_revs
        self._soft_start_revs = soft_start_revs
        self._soft_start_speed = soft_start_speed
        self._min_speed = min_speed

    def duty(self, moved, remaining):
        """The duty for a drive that has turned moved revolutions, however
        the travel goes, and has remaining left to its end, NaN if the travel
        is unknown."""
        speed = min(self._soft_start(moved), self._slowdown(remaining))
        speed = max(self._min_speed, min(self._cruise, speed))
        return -int(round(speed)) if self._up else int(round(speed))

    def _soft_start(self, moved):
        """The soft start's speed: from the soft start speed up to cruise
        over the first revolutions."""
        if self._soft_start_revs <= 0:
            return self._cruise
        rise = _smoothstep(moved / self._soft_start_revs)
        return self._soft_start_speed + (self._cruise - self._soft_start_speed) * rise

    def _slowdown(self, remaining):
        """The slowdown's speed: cruise until the last revolutions, down to
        the approach speed over them, and the approach speed from the end of
        the travel on, and with the travel unknown."""
        if math.isnan(remaining):
            return self._approach
        if self._slowdown_revs <= 0:
            return self._cruise if remaining > 0 else self._approach
        fall = _smoothstep(remaining / self._slowdown_revs)
        return self._approach + (self._cruise - self._approach) * fall


class Updates:
    """Paces a drive's speed writes, which go out without the read-back check
    the start and the stop are verified with: at most one every update_ms,
    and only when the duty has changed. It starts from the duty the lift
    started at, written at now_ms."""

    def __init__(self, duty, now_ms, update_ms=UPDATE_MS):
        self._duty = duty
        self._written_ms = now_ms
        self._update_ms = update_ms

    def due(self, now_ms, duty):
        """Whether duty goes out now, which counts it as written."""
        if duty == self._duty or now_ms - self._written_ms < self._update_ms:
            return False
        self._duty = duty
        self._written_ms = now_ms
        return True
