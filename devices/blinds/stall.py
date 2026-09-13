"""Deciding that the lift servo has stalled, from its samples during a move.
Pure, so the host tests run it."""


class StallDetector:
    """Decides "stalled" once the servo angle has stayed frozen, with the
    speed about 0, for the stall window. After a duty command the servo gets
    a start-up grace, so one that never starts is stalled once it's over."""

    def __init__(self, window_ms=150, max_speed=20, max_angle_change=5, grace_ms=300):
        self._window_ms = window_ms
        self._max_speed = max_speed
        self._max_angle_change = max_angle_change
        self._grace_ms = grace_ms
        self._duty = None
        self._commanded_at = None
        self._frozen_since = None
        self._frozen_angle = None
        self._outlier = False
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
            self._outlier = False
        elif self._frozen_since is not None and not self._outlier:
            # Near the wrap a single sample reads a stray angle or an absurd
            # speed. It's skipped; a second one in a row ends the frozen run.
            self._outlier = True
            return False
        elif still:
            self._frozen_since = t_ms
            self._frozen_angle = angle
            self._outlier = False
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
