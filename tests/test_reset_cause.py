"""The reset cause at boot (host test 3, boot part): which cause is
published, when the stored one is cleared, and the one restart after a
watchdog reset.

NVM is a bytearray here. It survives every reset, so a stored cause counts
only after a software reset, which is how every firmware-triggered restart
happens. The chip's reasons are the names of microcontroller.ResetReason's
members in CircuitPython 9.1 (shared-bindings/microcontroller/ResetReason.c).
"""

import contextlib
import sys
import types
import unittest
from unittest import mock

import reset_cause
from reset_cause import NVM_OFFSET, OPTIONS, boot_decision

CHIP_REASONS = ("POWER_ON", "BROWNOUT", "SOFTWARE", "DEEP_SLEEP_ALARM", "RESET_PIN",
                "WATCHDOG", "UNKNOWN", "RESCUE_DEBUG")


def boot(nvm, chip_reason):
    """One boot as code.py runs it: decide, write what the decision says to
    NVM, and return the cause to publish and whether to restart."""
    cause, restart, to_write = boot_decision(bytes(nvm[0:2]), chip_reason)
    if to_write is not None:
        nvm[0:2] = to_write
    return cause, restart


class WatchdogRestartTest(unittest.TestCase):
    def test_a_watchdog_reset_restarts_once_then_publishes_watchdog(self):
        nvm = bytearray(2)

        self.assertEqual(boot(nvm, "WATCHDOG"), (None, True))
        # The restart is a software reset, which keeps NVM.
        self.assertEqual(boot(nvm, "SOFTWARE"), ("watchdog", False))
        # Cleared, so a soft reload after it publishes the chip's reason.
        self.assertEqual(boot(nvm, "SOFTWARE"), ("software", False))


    def test_a_watchdog_reset_with_a_stale_cause_still_restarts_once(self):
        # The stale MQTT escalation doesn't count after a watchdog reset: the
        # restart stores watchdog over it.
        nvm = bytearray((0xB1, 3))

        self.assertEqual(boot(nvm, "WATCHDOG"), (None, True))
        self.assertEqual(boot(nvm, "SOFTWARE"), ("watchdog", False))


class ChipReasonTest(unittest.TestCase):
    def test_every_chip_reason_maps_to_an_option_of_the_entity(self):
        # The watchdog restarts first, and its follow-up boot publishes it.
        expected = {"POWER_ON": "power_on", "BROWNOUT": "brownout", "SOFTWARE": "software",
                    "DEEP_SLEEP_ALARM": "deep_sleep_alarm", "RESET_PIN": "reset_pin",
                    # HA takes "unknown" as no value, so these are "other".
                    "UNKNOWN": "other", "RESCUE_DEBUG": "other"}
        self.assertCountEqual(list(expected) + ["WATCHDOG"], CHIP_REASONS)
        for reason, cause in expected.items():
            with self.subTest(reason=reason):
                self.assertEqual(boot(bytearray(2), reason), (cause, False))
                self.assertIn(cause, OPTIONS)

    def test_a_reason_this_build_doesnt_know_is_other(self):
        self.assertEqual(boot(bytearray(2), "SOMETHING_NEW"), ("other", False))

    def test_the_options_are_the_chip_reasons_the_firmware_causes_and_other(self):
        # As allocated in #14, plus one safe-mode cause per reason (#104).
        self.assertCountEqual(OPTIONS, ["power_on", "reset_pin", "watchdog", "software",
                                        "deep_sleep_alarm", "brownout", "other_safe_mode",
                                        "mqtt_escalation", "restart_loop", "other"]
                              + list(SAFE_MODE_CAUSES.values()))


# The codes at offset 1, as decided in #13 and #41, then one per safe-mode
# reason (#104). A stored code outlives a deploy, so none may change.
SAFE_MODE_CAUSES = {6: "safe_mode_flash_write_fail", 7: "safe_mode_gc_alloc_outside_vm",
                    8: "safe_mode_hard_fault", 9: "safe_mode_interrupt_error",
                    10: "safe_mode_nlr_jump_fail", 11: "safe_mode_no_heap",
                    12: "safe_mode_programmatic", 13: "safe_mode_sdk_fatal_error",
                    14: "safe_mode_stack_overflow", 15: "safe_mode_watchdog"}
STORED_CAUSES = {1: "brownout", 2: "other_safe_mode", 3: "mqtt_escalation",
                 4: "restart_loop", 5: "watchdog", **SAFE_MODE_CAUSES}


@contextlib.contextmanager
def fake_microcontroller(nvm):
    """microcontroller with nvm as its NVM. Yields its reset(), a mock."""
    fake = types.SimpleNamespace(nvm=nvm, reset=mock.Mock())
    with mock.patch.dict(sys.modules, microcontroller=fake):
        yield fake.reset


class StoredCauseTest(unittest.TestCase):
    def test_each_stored_cause_is_published_and_cleared(self):
        for code, cause in STORED_CAUSES.items():
            with self.subTest(cause=cause):
                nvm = bytearray((0xB1, code))

                self.assertEqual(boot(nvm, "SOFTWARE"), (cause, False))
                # Both bytes zeroed, so a soft reload doesn't publish it again.
                self.assertEqual(nvm, bytearray(2))

    def test_a_stored_code_this_build_doesnt_know_is_other(self):
        nvm = bytearray((0xB1, 200))

        self.assertEqual(boot(nvm, "SOFTWARE"), ("other", False))
        self.assertEqual(nvm, bytearray(2))

    def test_each_cause_is_stored_by_restart_and_published_after_the_software_reset(self):
        for code, cause in STORED_CAUSES.items():
            with self.subTest(cause=cause):
                nvm = bytearray(NVM_OFFSET + 2)

                with fake_microcontroller(nvm) as reset:
                    reset_cause.restart(code)

                reset.assert_called_once_with()
                self.assertEqual(boot_decision(bytes(nvm[NVM_OFFSET:]), "SOFTWARE")[0], cause)

    def test_nothing_is_written_when_nothing_is_stored(self):
        # NVM is flash, so a boot with nothing stored leaves it alone, blank
        # (zeros or 0xFF) or cleared.
        for stored in (bytes(2), b"\xff\xff"):
            for reason in ("POWER_ON", "SOFTWARE", "DEEP_SLEEP_ALARM", "BROWNOUT"):
                with self.subTest(stored=stored, reason=reason):
                    self.assertIsNone(boot_decision(stored, reason)[2])

    def test_a_stored_cause_found_after_any_other_reset_is_stale(self):
        # For example, the power went between storing an MQTT escalation and
        # the reset. The chip's reason is published, and the record cleared.
        expected = {"POWER_ON": "power_on", "BROWNOUT": "brownout",
                    "RESET_PIN": "reset_pin", "DEEP_SLEEP_ALARM": "deep_sleep_alarm"}
        for reason, cause in expected.items():
            with self.subTest(reason=reason):
                nvm = bytearray((0xB1, 3))

                self.assertEqual(boot(nvm, reason), (cause, False))
                self.assertEqual(nvm, bytearray(2))


if __name__ == "__main__":
    unittest.main()
