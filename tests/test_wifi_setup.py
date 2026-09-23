"""The hostname is set before the radio connects, and only when asked for."""

import unittest

from wifi_setup import apply_hostname


class FakeRadio:
    """wifi.radio, as far as the hostname goes. CircuitPython rejects an
    empty or over-long name, so setting one is not free."""

    def __init__(self):
        self.hostname = "cpy-4fa480"
        self.writes = 0

    def __setattr__(self, name, value):
        if name == "hostname" and "hostname" in self.__dict__:
            self.__dict__["writes"] += 1
        self.__dict__[name] = value


class ApplyHostnameTest(unittest.TestCase):
    def test_a_name_from_settings_is_set_on_the_radio(self):
        radio = FakeRadio()

        self.assertEqual(apply_hostname(radio, "left-blinds"), "left-blinds")
        self.assertEqual(radio.hostname, "left-blinds")

    def test_no_setting_leaves_circuitpythons_own_choice(self):
        radio = FakeRadio()

        self.assertIsNone(apply_hostname(radio, None))
        self.assertEqual(radio.hostname, "cpy-4fa480")
        self.assertEqual(radio.writes, 0)

    def test_an_empty_setting_leaves_it_too(self):
        """A key present but blank means "no opinion", not a hostname of ""
        that CircuitPython would reject."""
        radio = FakeRadio()

        self.assertIsNone(apply_hostname(radio, ""))
        self.assertEqual(radio.hostname, "cpy-4fa480")
        self.assertEqual(radio.writes, 0)


if __name__ == "__main__":
    unittest.main()
