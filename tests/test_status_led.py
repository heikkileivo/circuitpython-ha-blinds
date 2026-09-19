"""What a blind's status LED shows (#116): dim and solid while all is well,
a slow blink at full brightness on an attention condition. The most severe
attention condition wins, and any of them beats a move.
"""

import unittest

import cover_state
import servo_health
from color import Color
from cover_state import SETTLED
from status_led import BLINK, BLINK_S, DIM, FULL, SOLID, decision


class EachRowTest(unittest.TestCase):
    def test_servo_health_no_reply_or_error_blinks_red(self):
        for health in (servo_health.NO_REPLY, servo_health.ERROR):
            with self.subTest(health=health):
                self.assertEqual(decision(health, True, cover_state.UP, False),
                                 (Color.RED, BLINK, FULL))

    def test_mqtt_disconnected_blinks_orange(self):
        self.assertEqual(decision(servo_health.OK, False, cover_state.UP, False),
                         (Color.ORANGE, BLINK, FULL))

    def test_an_unknown_cover_state_blinks_yellow(self):
        self.assertEqual(decision(servo_health.OK, True, cover_state.UNKNOWN, False),
                         (Color.YELLOW, BLINK, FULL))

    def test_a_move_is_dim_solid_blue(self):
        for state in (cover_state.MOVING_UP, cover_state.MOVING_DOWN) + SETTLED:
            # A tilt-only move leaves the cover state settled.
            with self.subTest(state=state):
                self.assertEqual(decision(servo_health.OK, True, state, True),
                                 (Color.BLUE, SOLID, DIM))

    def test_idle_open_closed_or_stopped_is_dim_solid_green(self):
        for state in SETTLED:
            with self.subTest(state=state):
                self.assertEqual(decision(servo_health.OK, True, state, False),
                                 (Color.GREEN, SOLID, DIM))


class PrioritiesTest(unittest.TestCase):
    def test_servo_health_beats_mqtt_disconnected_and_an_unknown_cover_state(self):
        for connected in (True, False):
            for state in (cover_state.UP, cover_state.UNKNOWN):
                with self.subTest(connected=connected, state=state):
                    self.assertEqual(decision(servo_health.ERROR, connected, state, False)[0],
                                     Color.RED)

    def test_mqtt_disconnected_beats_an_unknown_cover_state(self):
        self.assertEqual(decision(servo_health.OK, False, cover_state.UNKNOWN, False)[0],
                         Color.ORANGE)

    def test_each_attention_condition_beats_a_move(self):
        cases = ((servo_health.NO_REPLY, True, cover_state.MOVING_UP, Color.RED),
                 (servo_health.OK, False, cover_state.MOVING_DOWN, Color.ORANGE),
                 (servo_health.OK, True, cover_state.UNKNOWN, Color.YELLOW))
        for health, connected, state, color in cases:
            with self.subTest(color=color):
                self.assertEqual(decision(health, connected, state, True),
                                 (color, BLINK, FULL))


class BrightnessTest(unittest.TestCase):
    def test_dim_is_in_the_agreed_range_and_full_is_boots_brightness(self):
        self.assertTrue(0.03 <= DIM <= 0.05)
        self.assertEqual(FULL, 0.3)

    def test_the_slow_blink_is_about_a_second_each_way(self):
        self.assertEqual(BLINK_S, 1)


if __name__ == "__main__":
    unittest.main()
