"""The Servo drives an STS3215 lift (#140): duty in, goal speed out, and the
angle scaled to the SCS's counts so the travel count and the stall detector
work unchanged. Run on the fake servo bus, with the device-only modules
stubbed so blinds.py imports on the host."""

import sys
import types
import unittest

sys.modules.setdefault("microcontroller", types.SimpleNamespace(nvm=bytearray(256)))

import blinds
import travel
from packet import Address, Reader
from servo_health import detect_series, stop_lift
from tests.test_servo_health import FakeBus, FakeServo

STS_REGISTERS = (0, 0, 0, 9, 3)


def sts_lift():
    servo = FakeServo(1, registers=STS_REGISTERS, torque=1)
    reader = Reader(FakeBus(servo))
    detect_series(reader)
    return blinds.Servo(1, reader), servo, reader


def goal_speed(servo):
    word = servo.memory[Address.GOAL_SPEED_L] | servo.memory[Address.GOAL_SPEED_H] << 8
    return -(word & 0x7FFF) if word & 0x8000 else word


class SpeedTest(unittest.TestCase):
    def test_up_is_a_negative_duty_and_a_positive_speed(self):
        self.assertEqual(blinds.sts_speed(-800), 1600)
        self.assertEqual(blinds.sts_speed(500), -1000)
        self.assertEqual(blinds.sts_speed(0), 0)

    def test_the_speed_is_capped(self):
        self.assertEqual(blinds.sts_speed(-5000), blinds.STS_MAX_SPEED)
        self.assertEqual(blinds.sts_speed(5000), -blinds.STS_MAX_SPEED)


class DriveTest(unittest.TestCase):
    def test_a_duty_update_writes_the_goal_speed(self):
        lift, servo, _ = sts_lift()

        self.assertTrue(lift.set_duty(-300))
        self.assertEqual(goal_speed(servo), 600)
        self.assertTrue(lift.set_duty(450))
        self.assertEqual(goal_speed(servo), -900)
        self.assertEqual(lift.commanded_duty, 450)

    def test_the_speed_setter_confirms_by_reading_the_goal_speed_back(self):
        lift, servo, _ = sts_lift()

        lift.speed = -800

        self.assertEqual(goal_speed(servo), 1600)
        self.assertEqual(lift.speed, -800)

    def test_the_speed_setter_fails_if_the_servo_never_takes_it(self):
        lift, servo, _ = sts_lift()
        servo.ignored_writes = 99

        with self.assertRaises(blinds.ServoCommFailure):
            lift.speed = -800

    def test_duty_0_stops_it_and_the_brake_is_torque_1(self):
        lift, servo, reader = sts_lift()
        lift.set_duty(-800)

        self.assertEqual(lift._write_duty_0(), "confirmed")
        stop_lift(reader, "confirmed")

        self.assertEqual(goal_speed(servo), 0)
        self.assertEqual(servo.torque, 1)
        self.assertEqual(lift.commanded_duty, 0)

    def test_the_goal_time_is_never_written(self):
        lift, _, reader = sts_lift()

        lift.set_duty(-800)

        self.assertTrue(all(params[0] != Address.GOAL_TIME_L
                            for _, i, params in reader.uart.requests if i == 3))


class SampleTest(unittest.TestCase):
    def read(self, angle, speed):
        lift, servo, _ = sts_lift()
        # Present position, speed (sign in bit 15), load, voltage: little-endian.
        word = abs(speed) | (0x8000 if speed < 0 else 0)
        servo.memory[Address.PRESENT_POSITION_L:Address.PRESENT_VOLTAGE + 1] = bytes(
            (angle & 0xFF, angle >> 8, word & 0xFF, word >> 8, 0x20, 0x07, 85))
        return lift.angle_and_speed, lift

    def test_a_turn_is_the_same_number_of_counts_as_the_scs_travel_count_assumes(self):
        (angle, _), _ = self.read(4095, 0)

        self.assertEqual(angle, travel.COUNTS_PER_TURN - 1)
        self.assertEqual(self.read(2048, 0)[0][0], travel.COUNTS_PER_TURN // 2)
        self.assertEqual(self.read(0, 0)[0][0], 0)

    def test_the_speed_is_scaled_the_same_way_and_keeps_its_sign(self):
        (_, speed), _ = self.read(100, 1200)
        self.assertEqual(speed, 1200 * travel.COUNTS_PER_TURN // 4096)
        (_, speed), _ = self.read(100, -1200)
        self.assertEqual(speed, -(1200 * travel.COUNTS_PER_TURN // 4096))

    def test_a_stopped_lift_reads_speed_0_so_the_stall_detector_sees_it(self):
        (_, speed), _ = self.read(100, 0)
        self.assertEqual(speed, 0)

    def test_the_sample_feeds_the_moves_load_and_supply(self):
        _, lift = self.read(100, 600)

        self.assertEqual(lift.move.min_voltage, 85)
        self.assertEqual(lift.move.peak_load, 800)

    def test_a_turn_counted_through_the_scaled_angles_is_one_revolution(self):
        counter = travel.WrapCounter(True)
        for step in range(0, 4096, 128):
            counter.feed(self.read(step, 600)[0][0])
        counter.feed(self.read(0, 600)[0][0])

        self.assertAlmostEqual(counter.revs, 1.0, delta=0.05)


class StallTest(unittest.TestCase):
    def test_an_sts_has_no_dead_zone_so_a_stall_near_the_wrap_uses_the_normal_window(self):
        from stall import StallDetector, WINDOW_MS, GRACE_MS
        scs = StallDetector()
        sts = StallDetector(dead_zone=False)
        for detector in (scs, sts):
            stalled_at = None
            for t in range(0, 2000, 50):
                if detector.feed(t, 1100, 0, -300):
                    stalled_at = t
                    break
            detector.stalled_at = stalled_at

        self.assertGreaterEqual(sts.stalled_at, GRACE_MS)
        self.assertLessEqual(sts.stalled_at, GRACE_MS + WINDOW_MS)
        self.assertGreater(scs.stalled_at, sts.stalled_at)

    def test_the_lift_servo_says_whether_it_has_a_dead_zone(self):
        self.assertFalse(sts_lift()[0].has_dead_zone)
        scs = blinds.Servo(1, Reader(FakeBus(FakeServo(1))))
        self.assertTrue(scs.has_dead_zone)


if __name__ == "__main__":
    unittest.main()
