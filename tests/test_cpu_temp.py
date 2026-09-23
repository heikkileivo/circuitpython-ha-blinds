"""The CPU die temperature's plausibility check (#127).

ESP-IDF 5.2.2 latches the sensor 55.76 °C low until a reset. While the board
is idle that reads about -28 °C, which any floor catches; on the hot
afternoon this sensor exists to measure it reads about 20 °C, which does not
look wrong at all. Hence the comparison with the cavity.
"""
import unittest

from cpu_temp import MAX_BELOW_CAVITY_C, MAX_C, MIN_C, cavity, plausible

# The latch: two range steps of 27.88 °C.
LATCH_C = 55.76


def health(lift=None, tilt=None):
    """A servo-health message, as servo_health.health_message() builds it."""
    message = {"health": "ok"}
    for servo, temperature in (("lift", lift), ("tilt", tilt)):
        message[servo] = {"health": "ok", "temperature": temperature}
    return message


class PlausibleTest(unittest.TestCase):
    def test_an_idle_board_reads_plausible(self):
        # What the three blinds read the night the sensor went in, over a
        # cavity of 13-15 °C.
        for temperature in (31.9, 29.6, 32.2, 37.2):
            self.assertTrue(plausible(temperature, 14), temperature)

    def test_a_hot_afternoon_reads_plausible(self):
        # The die runs about 20 °C over the cavity, which the sun takes to
        # 56 °C, so the readings this exists to capture must pass.
        self.assertTrue(plausible(76.0, 56))
        self.assertTrue(plausible(MAX_C, 56))

    def test_the_latched_sensor_is_rejected_while_idle(self):
        # Left's band while latched, against the cavity it sat in.
        for temperature in (-22.1, -25.1, -28.1):
            self.assertFalse(plausible(temperature, 14), temperature)

    def test_the_latched_sensor_is_rejected_on_a_hot_afternoon(self):
        # The case a fixed floor misses: 76 - 55.76 = 20.24 °C, which is a
        # perfectly ordinary board temperature — but not beside a 56 °C
        # cavity, and this is the reading that would corrupt the answer.
        latched = 76.0 - LATCH_C
        self.assertTrue(latched > MIN_C, "the floor alone would let this pass")
        self.assertFalse(plausible(latched, 56))

    def test_a_latch_is_rejected_at_every_cavity_the_blinds_see(self):
        for cavity_c in range(10, 60, 5):
            die = cavity_c + 20.0          # the normal lead
            self.assertTrue(plausible(die, cavity_c), cavity_c)
            self.assertFalse(plausible(die - LATCH_C, cavity_c), cavity_c)

    def test_a_cold_board_close_to_its_cavity_still_passes(self):
        # Just after a cold boot the die has not pulled ahead yet.
        self.assertTrue(plausible(20.0, 20))
        self.assertTrue(plausible(20.0 - MAX_BELOW_CAVITY_C, 20))

    def test_without_a_cavity_reading_only_the_floor_applies(self):
        # Before the first servo read, or when neither servo replies.
        self.assertTrue(plausible(31.9, None))
        self.assertFalse(plausible(-28.1, None))

    def test_nothing_is_not_a_reading(self):
        self.assertFalse(plausible(None, 14))

    def test_a_nan_is_not_a_reading(self):
        # The original ESP32 returns NAN, and NaN fails every comparison.
        self.assertFalse(plausible(float("nan"), 14))

    def test_the_absolute_band_holds(self):
        self.assertFalse(plausible(MIN_C - 0.1, None))
        self.assertTrue(plausible(MIN_C, None))
        self.assertFalse(plausible(MAX_C + 0.1, None))


class CavityTest(unittest.TestCase):
    def test_the_cooler_servo_is_the_air(self):
        # A failing lift heats itself; the limp tilt beside it is the better
        # proxy, and taking the cooler one avoids rejecting good readings.
        self.assertEqual(cavity(health(lift=56, tilt=49)), 49)

    def test_one_servo_is_enough(self):
        self.assertEqual(cavity(health(lift=None, tilt=44)), 44)
        self.assertEqual(cavity(health(lift=41, tilt=None)), 41)

    def test_no_reply_from_either_servo_gives_nothing(self):
        self.assertIsNone(cavity(health()))

    def test_a_message_without_the_servos_gives_nothing(self):
        self.assertIsNone(cavity({"health": "no_reply"}))
