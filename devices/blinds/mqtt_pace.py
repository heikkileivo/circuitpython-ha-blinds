"""When the MQTT service task calls loop(), which blocks asyncio (#5, #55).
Pure, so the host tests run it."""


class Pace:
    def __init__(self, interval_ms):
        self._interval_ms = interval_ms
        self._last_ms = None        # When the last loop() call started

    def due(self, t_ms, moving, can_pause):
        """Whether to call loop() now."""
        if not moving:
            return True
        if not can_pause:
            return False
        return self._last_ms is None or t_ms - self._last_ms >= self._interval_ms

    def looped(self, t_ms):
        """A loop() call started at t_ms."""
        self._last_ms = t_ms
