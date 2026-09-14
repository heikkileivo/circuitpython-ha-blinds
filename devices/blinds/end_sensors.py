"""The two end sensors, read through one keypad.Keys on their pins, up then
down. It scans them in the background, so an end sensor edge isn't missed
while Python is busy in a UART read. Pure, so the host tests run it.

keypad only reports presses and releases, and a new Keys gives none for a
sensor that's already active. A level is read with keys.reset() instead,
which scans at once, with debounce_threshold 1, and reports the keys that
differ from the state it presets: the released keys before CircuitPython
9.2.1, the pressed keys since (adafruit/circuitpython c5a929e)."""

UP = 0
DOWN = 1


class EndSensors:
    def __init__(self, keys, reset_reports_pressed):
        """keys is the keypad.Keys on the up and down end sensors' pins.
        reset_reports_pressed is whether its reset() reports the pressed
        keys, rather than the released ones."""
        self._keys = keys
        self._reset_reports_pressed = reset_reports_pressed
        self._pressed = [False, False]  # Each end sensor's press since watch()

    def active(self, sensor):
        """Whether the end sensor, UP or DOWN, is active now."""
        return self._levels()[sensor]

    def watch(self, sensor):
        """Start watching an end sensor for a move, which reached() then
        polls. Returns whether it's active already."""
        active = self._levels()[sensor]
        self._pressed = [False, False]
        return active

    def reached(self, sensor):
        """Whether the end sensor has been pressed since watch(). A press
        counts even if a release follows before this call: the move stops
        on the first press."""
        self._drain()
        if self._keys.events.overflowed:
            # The full queue dropped events, maybe a press: reading the
            # levels counts an active end sensor as pressed.
            self._levels()
        return self._pressed[sensor]

    def _drain(self):
        """Take the queued events. A press counts for reached()."""
        events = self._keys.events
        while True:
            event = events.get()
            if event is None:
                return
            if event.pressed:
                self._pressed[event.key_number] = True

    def _levels(self):
        """Both end sensors' levels, from a reset of the scanner. A press
        queued before it, or an active end sensor, counts for reached(), so
        reading a level during a move doesn't lose its press."""
        self._drain()
        events = self._keys.events
        events.clear()
        self._keys.reset()
        # A key the reset doesn't report is in the state it presets. A scan
        # right after it may report a change too: the last event wins.
        levels = [not self._reset_reports_pressed] * 2
        while True:
            event = events.get()
            if event is None:
                break
            levels[event.key_number] = event.pressed
        for key, active in enumerate(levels):
            self._pressed[key] = self._pressed[key] or active
        return levels
