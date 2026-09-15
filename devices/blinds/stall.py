"""Deciding that the lift servo has stalled, from its samples during a move.
Pure, so the host tests run it."""

# Values, which the stall_* settings override, kept at stage 7's gate (#54).
# The bench saw the speed read 0 and the servo angle freeze within about
# 100 ms at the head rail. At the gate, the stall stop fired 197 ms after the
# angle froze there: the window, plus up to one 50 ms sample.
WINDOW_MS = 150
MAX_SPEED = 20          # counts/s
MAX_ANGLE_CHANGE = 5    # counts
GRACE_MS = 300
# A run frozen in the dead zone needs this window at duty 800, and 800 /
# |duty| times it at other duties.
DEAD_ZONE_WINDOW_MS = 200

# The pot's dead zone at the wrap, measured on Middle's lift (#78). While the
# lift servo turns through it, the servo angle holds at about 1018-1022, then
# 0-1, with the speed at 0. Crossing it took about 100 ms at duty 800, and
# longer in proportion at lower duties.
DEAD_ZONE_LOW_END = 10
DEAD_ZONE_HIGH_START = 1013
# The duty DEAD_ZONE_WINDOW_MS is given at: full speed, default_speed's 800.
_DEAD_ZONE_WINDOW_DUTY = 800


def in_dead_zone(angle):
    """Whether a servo angle is in the pot's dead zone."""
    return angle <= DEAD_ZONE_LOW_END or angle >= DEAD_ZONE_HIGH_START


class StallDetector:
    """Decides "stalled" once the servo angle has stayed frozen, with the
    speed about 0, for the stall window. A run that froze in the dead zone
    needs the dead-zone window instead, as the angle holds there while the
    servo still turns; frozen_angle, where the run froze, decides which.
    After a duty command the servo gets a start-up grace, so one that never
    starts is stalled once it's over."""

    def __init__(self, window_ms=WINDOW_MS, max_speed=MAX_SPEED,
                 max_angle_change=MAX_ANGLE_CHANGE, grace_ms=GRACE_MS,
                 dead_zone_window_ms=DEAD_ZONE_WINDOW_MS):
        self._window_ms = window_ms
        self._max_speed = max_speed
        self._max_angle_change = max_angle_change
        self._grace_ms = grace_ms
        self._dead_zone_window_ms = dead_zone_window_ms
        self._duty = None
        self._commanded_at = None
        self._frozen_since = None
        self._skipped_outlier = False
        self.frozen_angle = None
        self.frozen_ms = 0

    def feed(self, t_ms, angle, speed, duty):
        """Take one sample. Returns True if the lift has stalled."""
        if duty != self._duty:
            self._duty = duty
            self._commanded_at = t_ms
        if duty == 0:
            # Not driving, so not stalled, however still it is.
            self._frozen_since = None
            return False
        still = abs(speed) <= self._max_speed
        if self._frozen_since is not None and still and self._near_frozen_angle(angle):
            self._skipped_outlier = False
        elif self._frozen_since is not None and not self._skipped_outlier:
            # Near the wrap a single sample reads a stray angle or an absurd
            # speed. It's skipped; a second one in a row ends the frozen run.
            self._skipped_outlier = True
            return False
        elif still:
            self._frozen_since = t_ms
            self.frozen_angle = angle
            self._skipped_outlier = False
        else:
            self._frozen_since = None
            return False
        self.frozen_ms = t_ms - self._frozen_since
        return (self.frozen_ms >= self._window(duty)
                and t_ms - self._commanded_at >= self._grace_ms)

    def _window(self, duty):
        """The frozen run's stall window at this duty. A run that froze just
        outside the dead zone keeps the normal window, even if it drifts in:
        crossing the dead zone, the angle holds at 1018 or more."""
        if not in_dead_zone(self.frozen_angle):
            return self._window_ms
        return max(self._window_ms,
                   self._dead_zone_window_ms * _DEAD_ZONE_WINDOW_DUTY // abs(duty))

    def _near_frozen_angle(self, angle):
        # The servo angle reads 0-1023 over a turn, so 1021 and 0 are 3
        # counts apart.
        change = abs(angle - self.frozen_angle) % 1024
        return min(change, 1024 - change) <= self._max_angle_change
