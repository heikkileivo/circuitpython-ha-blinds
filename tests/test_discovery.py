import unittest

from discovery import HADiscovery

MAC = bytes.fromhex("f412fa448000")


class DiscoveryTest(unittest.TestCase):
    def test_a_device_without_availability_gets_none(self):
        # The meters don't pass availability, so their payloads stay as they
        # were.
        disc = HADiscovery("Water Meter", "CircuitPython Water Meter", "water", mac=MAC)
        disc.add_component("water_total", "sensor", {"name": "Water total"})

        self.assertIsNone(disc.availability_topic)
        self.assertNotIn("availability", disc.discovery_payload_json())


if __name__ == "__main__":
    unittest.main()
