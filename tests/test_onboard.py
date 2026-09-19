"""Onboarding a new servo (#112): the onboard module's steps on a fake servo
bus, as the host tool (#113) runs them from the REPL.

Each step prints one marker line, MARKER then JSON, which is what the host
tool reads; the tests read the same lines. The fake servos model the baud
rate they answer at, their EEPROM, and LOCK, which decides whether a write
to the EEPROM survives a power cycle.

A factory servo, from #99: ID 1 at 1 Mbps (baud code 0), with the SCS15's
angle limits, 20/1003. What a factory SCS115's LOCK powers up as is unknown,
so it powers up locked here, the case that needs the unlock.
"""

import json
import math
import unittest

import cover_state
import persist
from onboard import MARKER, Onboarding
from packet import Address
from tests.test_servo_health import FakeBus, FakeServo


def factory_servo(**changes):
    settings = dict(baud_code=0, angle_limits=(20, 1003), lock=1)
    settings.update(changes)
    return FakeServo(1, **settings)


class Run:
    """An Onboarding on a fake bus, with the marker lines it printed."""

    def __init__(self, *servos, nvm=None):
        self.bus = FakeBus(*servos)
        self.lines = []
        store = None if nvm is None else persist.Store(nvm)
        self.onboarding = Onboarding(self.bus, store, out=self.lines.append)

    def markers(self):
        return [json.loads(line[len(MARKER):]) for line in self.lines
                if line.startswith(MARKER)]

    def marker(self, step):
        (found,) = [marker for marker in self.markers() if marker["step"] == step]
        return found


class ScanTest(unittest.TestCase):
    def test_a_factory_servo_is_found_at_1_mbps_and_id_1(self):
        run = Run(factory_servo())

        self.assertTrue(run.onboarding.scan())

        self.assertEqual(run.marker("scan"), {
            "step": "scan", "ok": True, "found": [{"baud_rate": 1000000, "id": 1}],
            "garbled": []})


class OnboardTest(unittest.TestCase):
    def test_a_factory_servo_onboarded_as_the_lift_ends_at_id_1_250000_and_wheel_mode(self):
        servo = factory_servo()
        run = Run(servo)

        self.assertTrue(run.onboarding.onboard("lift"))

        self.assertEqual((servo.id, servo.baud_rate, servo.angle_limits), (1, 250000, (0, 0)))
        self.assertEqual(servo.lock, 1)
        self.assertEqual(run.bus.baudrate, 250000)
        self.assertEqual([m["step"] for m in run.markers()],
                         ["scan", "identify", "lock_off", "angle_limits", "id", "baud_rate",
                          "read_back", "lock_on"])
        self.assertTrue(all(m["ok"] for m in run.markers()))

    def test_a_factory_servo_onboarded_as_the_tilt_ends_at_id_2_250000_and_10_to_1000(self):
        servo = factory_servo()
        run = Run(servo)

        self.assertTrue(run.onboarding.onboard("tilt"))

        self.assertEqual((servo.id, servo.baud_rate, servo.angle_limits),
                         (2, 250000, (10, 1000)))
        self.assertEqual(servo.lock, 1)
        self.assertEqual(run.marker("read_back"), {
            "step": "read_back", "ok": True, "id": 2, "baud_rate": 250000,
            "angle_limits": [10, 1000]})

    def test_the_settings_survive_a_power_cycle(self):
        # A factory servo powers up locked, so this needs the unlock.
        servo = factory_servo()
        Run(servo).onboarding.onboard("tilt")

        servo.power_cycle()

        self.assertEqual((servo.id, servo.baud_rate, servo.angle_limits),
                         (2, 250000, (10, 1000)))

    def test_the_identify_step_reports_registers_0_to_4_and_the_series(self):
        run = Run(factory_servo(registers=(0, 4, 1, 5, 15)))

        run.onboarding.onboard("tilt")

        self.assertEqual(run.marker("identify"), {
            "step": "identify", "ok": True, "registers": [0, 4, 1, 5, 15], "series": "scs"})


class RefusalTest(unittest.TestCase):
    def assert_refused_with_no_writes(self, run, role="lift"):
        self.assertFalse(run.onboarding.onboard(role))
        self.assertEqual(run.bus.writes(), [])
        self.assertFalse(run.markers()[-1]["ok"])

    def test_two_servos_on_the_bus_are_refused_and_both_listed(self):
        # The other servo wasn't unplugged: the fitted tilt, at 250000.
        run = Run(factory_servo(), FakeServo(2))

        self.assert_refused_with_no_writes(run)
        self.assertEqual(run.marker("scan")["found"], [{"baud_rate": 1000000, "id": 1},
                                                      {"baud_rate": 250000, "id": 2}])

    def test_two_servos_at_the_same_id_and_rate_garble_each_other_and_are_refused(self):
        run = Run(factory_servo(), factory_servo())

        self.assert_refused_with_no_writes(run)
        self.assertEqual(run.marker("scan")["garbled"],
                         [{"baud_rate": 1000000, "id": 1, "problem": "bad checksum"}])

    def test_a_garbled_reply_is_refused(self):
        run = Run(factory_servo(garbles=True))

        self.assert_refused_with_no_writes(run)
        self.assertEqual(run.marker("scan")["found"], [])

    def test_an_sts3215_is_refused_as_unsupported(self):
        # Its model number, 777, reads 9, 3 at registers 3-4.
        run = Run(factory_servo(registers=(3, 9, 0, 9, 3)))

        self.assert_refused_with_no_writes(run)
        self.assertEqual(run.marker("identify"), {
            "step": "identify", "ok": False, "registers": [3, 9, 0, 9, 3], "series": "sts"})

    def test_a_role_that_isnt_lift_or_tilt_is_refused_before_the_scan(self):
        run = Run(factory_servo())

        self.assert_refused_with_no_writes(run, role="Lift")
        self.assertEqual(run.bus.requests, [])


class WriteOrderTest(unittest.TestCase):
    def test_lock_1_goes_to_the_new_id_at_the_new_rate(self):
        run = Run(factory_servo())

        run.onboarding.onboard("tilt")

        self.assertEqual(run.bus.writes()[-1], (250000, 2, bytes((Address.LOCK, 1))))

    def test_the_id_is_written_before_the_baud_rate(self):
        # So a new tilt is never at ID 1 on the 250000 bus.
        run = Run(factory_servo())

        run.onboarding.onboard("tilt")

        self.assertEqual([write[:2] + (write[2][0],) for write in run.bus.writes()], [
            (1000000, 1, Address.LOCK), (1000000, 1, Address.MIN_ANGLE_LIMIT_L),
            (1000000, 1, Address.ID), (1000000, 2, Address.BAUD_RATE),
            (250000, 2, Address.LOCK)])

    def test_a_half_done_servo_at_id_2_and_1_mbps_is_found_and_finished_by_a_rerun(self):
        # Its ID was written, then the onboarding stopped before the baud rate.
        servo = factory_servo(lock=0)
        servo.write(Address.ID, bytes((2,)))
        run = Run(servo)

        self.assertTrue(run.onboarding.onboard("tilt"))

        self.assertEqual(run.marker("scan")["found"], [{"baud_rate": 1000000, "id": 2}])
        self.assertEqual((servo.id, servo.baud_rate, servo.angle_limits, servo.lock),
                         (2, 250000, (10, 1000), 1))

    def test_a_servo_busy_writing_its_flash_is_read_again(self):
        # Its reply to the baud rate write can't say when that's done: it
        # comes at the old rate.
        servo = factory_servo(busy_after_eeprom_write=2)
        run = Run(servo)

        self.assertTrue(run.onboarding.onboard("tilt"))

        self.assertEqual((servo.id, servo.baud_rate, servo.lock), (2, 250000, 1))

    def test_a_write_that_doesnt_read_back_stops_the_onboarding_there(self):
        servo = factory_servo(refuses=[(Address.MIN_ANGLE_LIMIT_L, b"\x00\x0a\x03\xe8")])
        run = Run(servo)

        self.assertFalse(run.onboarding.onboard("tilt"))

        self.assertEqual(run.markers()[-1], {"step": "angle_limits", "ok": False,
                                             "angle_limits": [20, 1003]})
        # Nothing after it: the servo is still at ID 1 and 1 Mbps.
        self.assertEqual((servo.id, servo.baud_rate), (1, 1000000))


def onboarded(role, **changes):
    """A factory servo onboarded for role, then power-cycled."""
    servo = factory_servo(**changes)
    Run(servo).onboarding.onboard(role)
    servo.power_cycle()
    return servo


class VerifyTest(unittest.TestCase):
    def test_after_a_power_cycle_the_servo_answers_at_its_role_and_keeps_its_limits(self):
        run = Run(onboarded("tilt"))

        self.assertTrue(run.onboarding.verify("tilt"))

        self.assertEqual(run.marker("verify"), {
            "step": "verify", "ok": True, "found": [{"baud_rate": 250000, "id": 2}],
            "garbled": [], "angle_limits": [10, 1000]})

    def test_it_fails_when_the_angle_limits_didnt_persist(self):
        volatile = range(Address.MIN_ANGLE_LIMIT_L, Address.MAX_ANGLE_LIMIT_H + 1)
        run = Run(onboarded("tilt", volatile=volatile))

        self.assertFalse(run.onboarding.verify("tilt"))

        self.assertEqual(run.marker("verify")["angle_limits"], [20, 1003])

    def test_it_fails_when_the_id_or_baud_rate_didnt_persist(self):
        cases = {Address.ID: {"baud_rate": 250000, "id": 1},
                 Address.BAUD_RATE: {"baud_rate": 1000000, "id": 2}}
        for address, found in cases.items():
            with self.subTest(address=address):
                run = Run(onboarded("tilt", volatile=(address,)))

                self.assertFalse(run.onboarding.verify("tilt"))

                self.assertEqual(run.marker("verify")["found"], [found])

    def test_it_fails_for_the_other_role(self):
        run = Run(onboarded("tilt"))

        self.assertFalse(run.onboarding.verify("lift"))


class ForgetTravelTest(unittest.TestCase):
    RECORD = persist.encode(cover_state.DOWN, 3.5, 12.25)

    def test_a_lift_onboarding_marks_the_travel_unknown_and_keeps_the_full_travel(self):
        nvm = bytearray(self.RECORD) + bytearray(b"\xb1\x03")
        run = Run(nvm=nvm)

        self.assertTrue(run.onboarding.forget_travel("lift"))

        state, travel, full_travel = persist.decode(nvm[:persist.SIZE])
        self.assertEqual((state, full_travel), (cover_state.DOWN, 12.25))
        self.assertTrue(math.isnan(travel))
        # The reset cause after it is left alone.
        self.assertEqual(nvm[persist.SIZE:], b"\xb1\x03")
        self.assertEqual(run.marker("forget_travel"), {
            "step": "forget_travel", "ok": True, "forgot": True, "full_travel": 12.25})

    def test_a_full_travel_not_learned_is_reported_as_none(self):
        run = Run(nvm=bytearray(persist.encode(cover_state.UP, 0.0, persist.NAN)))

        run.onboarding.forget_travel("lift")

        self.assertIsNone(run.marker("forget_travel")["full_travel"])

    def test_a_blank_record_is_left_blank(self):
        # Its travel is unknown already, and its cover state can't be saved.
        nvm = bytearray(persist.SIZE)
        run = Run(nvm=nvm)

        self.assertTrue(run.onboarding.forget_travel("lift"))

        self.assertEqual(nvm, bytearray(persist.SIZE))
        self.assertFalse(run.marker("forget_travel")["forgot"])

    def test_a_travel_with_no_cover_state_fails_and_is_left_alone(self):
        # Only a corrupt record has one: the blind never saves that.
        nvm = bytearray(self.RECORD)
        nvm[2] = 0
        record = bytes(nvm)
        run = Run(nvm=nvm)

        self.assertFalse(run.onboarding.forget_travel("lift"))

        self.assertEqual(nvm, record)

    def test_a_tilt_onboarding_leaves_persistence_untouched(self):
        nvm = bytearray(self.RECORD)
        run = Run(nvm=nvm)

        self.assertTrue(run.onboarding.forget_travel("tilt"))

        self.assertEqual(nvm, self.RECORD)
        self.assertEqual(run.marker("forget_travel"), {
            "step": "forget_travel", "ok": True, "forgot": False})


class BusCheckTest(unittest.TestCase):
    def test_it_passes_with_the_lift_and_tilt_at_their_ids_and_limits(self):
        run = Run(FakeServo(1), FakeServo(2, angle_limits=(10, 1000)))

        self.assertTrue(run.onboarding.bus_check())

        self.assertEqual(run.marker("bus_check"), {
            "step": "bus_check", "ok": True,
            "found": [{"baud_rate": 250000, "id": 1}, {"baud_rate": 250000, "id": 2}],
            "garbled": [], "angle_limits": {"lift": [0, 0], "tilt": [10, 1000]}})

    def test_it_fails_unless_exactly_ids_1_and_2_answer_at_the_bus_rate(self):
        lift, tilt = FakeServo(1), FakeServo(2, angle_limits=(10, 1000))
        cases = {"only the lift": (lift,),
                 "a third servo": (lift, tilt, FakeServo(3)),
                 "the tilt at 1 Mbps": (lift, FakeServo(2, baud_code=0,
                                                        angle_limits=(10, 1000))),
                 "the tilt garbling": (lift, FakeServo(2, angle_limits=(10, 1000),
                                                       garbles=True))}
        for case, servos in cases.items():
            with self.subTest(case=case):
                self.assertFalse(Run(*servos).onboarding.bus_check())

    def test_it_fails_when_a_servos_limits_dont_match_its_role(self):
        # The tilt still at the SCS15's factory limits.
        run = Run(FakeServo(1), FakeServo(2, angle_limits=(20, 1003)))

        self.assertFalse(run.onboarding.bus_check())

        self.assertEqual(run.marker("bus_check")["angle_limits"],
                         {"lift": [0, 0], "tilt": [20, 1003]})

    def test_the_limits_are_left_unchecked_unless_both_are_scs(self):
        sts_tilt = FakeServo(2, angle_limits=(0, 4095), registers=(3, 9, 0, 9, 3))
        run = Run(FakeServo(1), sts_tilt)

        self.assertTrue(run.onboarding.bus_check())

        self.assertIsNone(run.marker("bus_check")["angle_limits"])


if __name__ == "__main__":
    unittest.main()
