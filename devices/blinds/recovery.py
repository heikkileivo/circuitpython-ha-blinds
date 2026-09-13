"""The last-resort recovery: when to restart the blind because its MQTT
link has been unhealthy too long. Pure, so the host tests run it."""

# How long the liveness echo may be missing before the blind restarts, which
# mqtt_escalation_s overrides.
WINDOW_MS = 300_000
# How many failed runs of main() in a row restart the blind, which
# restart_loop_max overrides.
MAX_FAILURES = 3


class Escalation:
    """Decides that the MQTT link has been unhealthy too long: the liveness
    echo has been missing for the window."""

    def __init__(self, t_ms, window_ms=WINDOW_MS):
        self._window_ms = window_ms
        self._since = t_ms

    def echo(self, t_ms):
        """The liveness echo arrived: the link is healthy, so the window
        starts again."""
        self._since = t_ms

    def due(self, t_ms, moving):
        """Whether to restart now. Never while the blind is moving: the
        restart waits for the move to end."""
        if moving:
            return False
        return t_ms - self._since >= self._window_ms


class RestartLoop:
    """Decides that main() keeps failing: max_failures quick failed runs in
    a row. A run that lasted longer than long_run_ms, the escalation
    window, came up and ran a while, so it resets the count."""

    def __init__(self, max_failures=MAX_FAILURES, long_run_ms=WINDOW_MS):
        self._max_failures = max_failures
        self._long_run_ms = long_run_ms
        self._failures = 0

    def failed(self, started_ms, ended_ms):
        """A run of main() that started and failed at these times ended.
        Returns whether to restart instead of running main() again."""
        if ended_ms - started_ms > self._long_run_ms:
            self._failures = 0
            return False
        self._failures += 1
        return self._failures >= self._max_failures
