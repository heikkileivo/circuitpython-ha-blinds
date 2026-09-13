"""Deciding that the lift servo has stalled, from its samples during a move.
Pure, so the host tests run it."""

# Starting values, which the stall_* settings override. The bench saw the
# speed read 0 and the servo angle freeze within about 100 ms at the head rail.
WINDOW_MS = 150
MAX_SPEED = 20          # counts/s
MAX_ANGLE_CHANGE = 5    # counts
GRACE_MS = 300


class StallDetector:
    """Decides "stalled" once the servo angle has stayed frozen, with the
    speed about 0, for the stall window. After a duty command the servo gets
    a start-up grace, so one that never starts is stalled once it's over."""

    def __init__(self, window_ms=WINDOW_MS, max_speed=MAX_SPEED,
                 max_angle_change=MAX_ANGLE_CHANGE, grace_ms=GRACE_MS):
        self._window_ms = window_ms
        self._max_speed = max_speed
        self._max_angle_change = max_angle_change
        self._grace_ms = grace_ms
        self._duty = None
        self._commanded_at = None
        self._frozen_since = None
        self._frozen_angle = None
        self._skipped_outlier = False
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
            self._frozen_angle = angle
            self._skipped_outlier = False
        else:
            self._frozen_since = None
            return False
        self.frozen_ms = t_ms - self._frozen_since
        return (self.frozen_ms >= self._window_ms
                and t_ms - self._commanded_at >= self._grace_ms)

    def _near_frozen_angle(self, angle):
        # The servo angle reads 0-1023 over a turn, so 1021 and 0 are 3
        # counts apart.
        change = abs(angle - self._frozen_angle) % 1024
        return min(change, 1024 - change) <= self._max_angle_change
