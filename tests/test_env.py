"""Reading settings.toml gives the type the caller asked for, on both the
CircuitPython that parses TOML integers and the one that doesn't.

Through 9.x, os.getenv() returns an int for a TOML integer and a str for
everything else. From 10.2.0 on it returns a str for every value (#128).
Both are exercised here, because the fleet runs both during the upgrade.
"""

import unittest

import env

# What os.getenv() makes of a settings.toml holding:
#
#   count = 800
#   scale = "10.5"
#   name = "Blinds"
#
# through CircuitPython 9.x, and from 10.2.0 on.
FIRMWARES = {
    "9.1.3": {"count": 800, "scale": "10.5", "name": "Blinds"},
    "10.3.1": {"count": "800", "scale": "10.5", "name": "Blinds"},
}


class EnvTest(unittest.TestCase):
    def setUp(self):
        self._getenv = env.os.getenv

    def tearDown(self):
        env.os.getenv = self._getenv

    def _settings(self, values):
        env.os.getenv = lambda key, default=None: values.get(key, default)

    def test_an_integer_reads_as_an_int_on_every_firmware(self):
        for firmware, values in FIRMWARES.items():
            with self.subTest(firmware=firmware):
                self._settings(values)

                self.assertEqual(env.integer("count", 1), 800)

    def test_a_fraction_reads_as_a_float_on_every_firmware(self):
        for firmware, values in FIRMWARES.items():
            with self.subTest(firmware=firmware):
                self._settings(values)

                self.assertEqual(env.number("scale", 1.0), 10.5)

    def test_an_integer_setting_reads_as_a_float_when_asked_for_one(self):
        for firmware, values in FIRMWARES.items():
            with self.subTest(firmware=firmware):
                self._settings(values)

                self.assertEqual(env.number("count", 1.0), 800.0)

    def test_text_reads_as_a_str_on_every_firmware(self):
        for firmware, values in FIRMWARES.items():
            with self.subTest(firmware=firmware):
                self._settings(values)

                self.assertEqual(env.text("name", "Meter"), "Blinds")

    def test_an_integer_setting_reads_as_a_str_when_asked_for_one(self):
        for firmware, values in FIRMWARES.items():
            with self.subTest(firmware=firmware):
                self._settings(values)

                self.assertEqual(env.text("count", ""), "800")

    def test_a_key_that_isnt_set_gives_the_default_back(self):
        self._settings({})

        self.assertEqual(env.integer("count", 800), 800)
        self.assertEqual(env.number("scale", 10.5), 10.5)
        self.assertEqual(env.text("name", "Blinds"), "Blinds")

    def test_a_default_that_isnt_given_reads_as_none(self):
        self._settings({})

        self.assertIsNone(env.integer("count"))
        self.assertIsNone(env.number("scale"))
        self.assertIsNone(env.text("name"))

    def test_a_default_is_handed_back_unconverted(self):
        """The default isn't parsed, so a caller that passes None, or a type
        of its own, gets it back as it was passed."""
        self._settings({})

        self.assertIsNone(env.number("scale", None))
        self.assertEqual(env.integer("count", "not a number"), "not a number")


if __name__ == "__main__":
    unittest.main()
