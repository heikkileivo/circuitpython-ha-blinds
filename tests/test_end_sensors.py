"""Reading the end sensors through one keypad.Keys, up then down.

keypad has no level read: it only reports presses and releases. The fake
below scans like CircuitPython's keypad with debounce_threshold 1
(shared-module/keypad): an event fires when a key's debounce counter
reaches 0, and a new Keys starts it at 0, so its first scan is silent. An
end sensor already active at boot gives no press event. reset() presets the
counters, then scans at once: to pressed before 9.2.1, so released keys give
a release event, and to released since (adafruit/circuitpython c5a929e), so
pressed keys give a press event.
"""

import unittest

from end_sensors import DOWN, UP, EndSensors

# Whether each firmware's keys.reset() reports the pressed keys, rather than
# the released ones.
FIRMWARES = {"9.1.1": False, "9.2.1": True}


class Event:
    def __init__(self, key_number, pressed):
        self.key_number = key_number
        self.pressed = pressed
        self.released = not pressed


class EventQueue:
    """A full queue drops the new event, and sets overflowed."""

    def __init__(self, max_events):
        self._events = []
        self._max_events = max_events
        self.overflowed = False

    def record(self, key_number, pressed):
        if len(self._events) == self._max_events:
            self.overflowed = True
            return
        self._events.append(Event(key_number, pressed))

    def get(self):
        return self._events.pop(0) if self._events else None

    def clear(self):
        self._events = []
        self.overflowed = False


class FakeKeys:
    """levels holds whether each sensor is active. scan() is one background
    scan, which keypad runs every interval."""

    def __init__(self, levels, reset_reports_pressed, max_events=64):
        self.levels = list(levels)
        self._reset_reports_pressed = reset_reports_pressed
        self.events = EventQueue(max_events)
        self._counters = [0] * len(self.levels)
        self.scan()

    def scan(self):
        for key, pressed in enumerate(self.levels):
            counter = self._counters[key]
            if pressed and counter < 1:
                counter += 1
                if counter == 0:
                    counter = 1
                    self.events.record(key, True)
            elif not pressed and counter > -1:
                counter -= 1
                if counter == 0:
                    counter = -1
                    self.events.record(key, False)
            self._counters[key] = counter

    def reset(self):
        self._counters = [-1 if self._reset_reports_pressed else 1] * len(self.levels)
        self.scan()

    def set(self, sensor, active):
        """The sensor goes active or inactive, and a scan sees it."""
        self.levels[sensor] = active
        self.scan()


def build(firmware, up=False, down=False, max_events=64):
    keys = FakeKeys([up, down], FIRMWARES[firmware], max_events)
    return keys, EndSensors(keys, FIRMWARES[firmware])


class ActiveTest(unittest.TestCase):
    def test_an_end_sensor_already_active_at_boot_reads_active(self):
        # The new Keys gave no press event for it.
        for firmware in FIRMWARES:
            for up, down in [(True, False), (False, True), (False, False)]:
                with self.subTest(firmware=firmware, up=up, down=down):
                    keys, sensors = build(firmware, up, down)

                    self.assertEqual((sensors.active(UP), sensors.active(DOWN)), (up, down))


class WatchTest(unittest.TestCase):
    def test_a_move_reaches_its_end_sensor_at_its_first_press(self):
        for firmware in FIRMWARES:
            with self.subTest(firmware=firmware):
                keys, sensors = build(firmware)

                self.assertFalse(sensors.watch(UP))
                self.assertFalse(sensors.reached(UP))
                keys.set(UP, True)
                self.assertTrue(sensors.reached(UP))

    def test_an_end_sensor_that_chatters_off_before_the_poll_still_counts(self):
        # The bench saw the up end sensor go off and on again within 190 ms,
        # and a poll can wait that long behind a UART read.
        for firmware in FIRMWARES:
            with self.subTest(firmware=firmware):
                keys, sensors = build(firmware)
                sensors.watch(UP)

                keys.set(UP, True)
                keys.set(UP, False)

                self.assertTrue(sensors.reached(UP))

    def test_going_active_before_the_move_started_doesnt_count(self):
        # The blind settled off the up end sensor overnight, and chattered
        # on its way.
        for firmware in FIRMWARES:
            with self.subTest(firmware=firmware):
                keys, sensors = build(firmware, up=True)
                keys.set(UP, False)
                keys.set(UP, True)
                keys.set(UP, False)

                self.assertFalse(sensors.watch(UP))
                self.assertFalse(sensors.reached(UP))

    def test_only_the_watched_end_sensor_counts(self):
        for firmware in FIRMWARES:
            with self.subTest(firmware=firmware):
                keys, sensors = build(firmware)
                sensors.watch(DOWN)

                keys.set(UP, True)

                self.assertFalse(sensors.reached(DOWN))

    def test_a_press_dropped_by_a_full_queue_is_found_from_the_level(self):
        for firmware in FIRMWARES:
            with self.subTest(firmware=firmware):
                keys, sensors = build(firmware, down=True, max_events=1)
                sensors.watch(UP)

                # The blind leaves the down end sensor, whose release fills
                # the queue, so the up end sensor's press is dropped.
                keys.set(DOWN, False)
                keys.set(UP, True)

                self.assertTrue(sensors.reached(UP))

    def test_reading_a_level_during_a_move_doesnt_lose_its_press(self):
        # The re-anchoring reads the levels while a move watches (#50).
        for firmware in FIRMWARES:
            with self.subTest(firmware=firmware):
                keys, sensors = build(firmware)
                sensors.watch(UP)

                keys.set(UP, True)
                keys.set(UP, False)
                sensors.active(DOWN)

                self.assertTrue(sensors.reached(UP))


if __name__ == "__main__":
    unittest.main()
