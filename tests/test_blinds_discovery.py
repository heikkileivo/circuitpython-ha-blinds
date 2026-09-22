import json
import unittest

from components import blinds_discovery

# Right blind's MAC and name, as in the budget decided in #14.
MAC = bytes.fromhex("f412fa448000")
DEVICE_NAME = "Upstairs Living Room Right Blinds"

# The broker takes a 5 KB publish since MiniMQTT 8.1.0; this leaves headroom.
BUDGET_BYTES = 4096


def blinds_payload():
    return blinds_discovery(DEVICE_NAME, mac=MAC).discovery_payload_json()


class BlindsDiscoveryTest(unittest.TestCase):
    def test_payload_fits_the_budget(self):
        size = len(blinds_payload().encode("utf-8"))

        self.assertLessEqual(size, BUDGET_BYTES)

    def test_availability_sits_once_at_the_device_root(self):
        # One root topic covers every entity. The payloads stay at HA's
        # defaults, online and offline, which the last will and the
        # on-connect publish use.
        payload = json.loads(blinds_payload())

        topic = payload.pop("availability_topic")

        self.assertEqual(topic, "blinds_f412fa448000/availability")
        # Nothing else: no per-component availability, no custom payloads.
        self.assertNotIn("availab", json.dumps(payload))

    def test_payload_is_compact_json(self):
        payload = blinds_payload()

        self.assertNotIn(", ", payload)
        self.assertNotIn(": ", payload)

    def test_kept_components_keep_their_unique_ids(self):
        # HA derives the entity IDs the automations use from these.
        components = json.loads(blinds_payload())["cmps"]

        for uid in (
            "blinds_f412fa448000_cover",
            "blinds_f412fa448000_speed",
            "blinds_f412fa448000_opened_count",
            "blinds_f412fa448000_uptime_seconds",
        ):
            self.assertEqual(components[uid]["unique_id"], uid)

    def test_dropped_components_are_removed_with_platform_only_entries(self):
        # A platform-only entry is how HA's device discovery removes one
        # component; leaving it out would leave the entity behind.
        components = json.loads(blinds_payload())["cmps"]

        self.assertEqual(components["blinds_f412fa448000_uptime"], {"p": "sensor"})
        self.assertEqual(components["blinds_f412fa448000_status_led"], {"p": "switch"})

    def test_reconnects_is_a_diagnostic_measurement_like_the_meters(self):
        reconnects = json.loads(blinds_payload())["cmps"]["blinds_f412fa448000_reconnects"]

        self.assertEqual(reconnects["p"], "sensor")
        self.assertEqual(reconnects["name"], "Reconnects")
        self.assertEqual(reconnects["entity_category"], "diagnostic")
        self.assertEqual(reconnects["state_class"], "measurement")
        self.assertEqual(reconnects["state_topic"], "blinds_f412fa448000/reconnects/state")

    def test_servo_health_is_a_diagnostic_enum_with_its_attributes_on_its_state_topic(self):
        # One JSON message carries both: the health for the state, the
        # per-servo figures for the attributes.
        health = json.loads(blinds_payload())["cmps"]["blinds_f412fa448000_servo_health"]

        self.assertEqual(health["p"], "sensor")
        self.assertEqual(health["name"], "Servo health")
        self.assertEqual(health["entity_category"], "diagnostic")
        self.assertEqual(health["device_class"], "enum")
        self.assertCountEqual(health["options"], ["ok", "no_reply", "error"])
        self.assertEqual(health["state_topic"], "blinds_f412fa448000/servo_health/state")
        self.assertEqual(health["value_template"], "{{ value_json.health }}")
        self.assertEqual(health["json_attributes_topic"], health["state_topic"])

    def test_cpu_temperature_is_a_diagnostic_sensor_on_its_own_short_topic(self):
        # The board's own temperature, next to the servos' cavity readings
        # (#125). The key is "cpu_temp", not "cpu_temperature": the payload
        # has about 30 bytes left against BUDGET_BYTES.
        cpu = json.loads(blinds_payload())["cmps"]["blinds_f412fa448000_cpu_temp"]

        self.assertEqual(cpu["p"], "sensor")
        self.assertEqual(cpu["name"], "CPU temperature")
        self.assertEqual(cpu["entity_category"], "diagnostic")
        self.assertEqual(cpu["device_class"], "temperature")
        self.assertEqual(cpu["unit_of_measurement"], "°C")
        self.assertEqual(cpu["state_class"], "measurement")
        self.assertEqual(cpu["state_topic"], "blinds_f412fa448000/cpu_temp/state")

    def test_reset_cause_is_a_diagnostic_enum_of_the_chip_reasons_and_firmware_causes(self):
        cause = json.loads(blinds_payload())["cmps"]["blinds_f412fa448000_reset_cause"]

        self.assertEqual(cause["p"], "sensor")
        self.assertEqual(cause["name"], "Reset cause")
        self.assertEqual(cause["entity_category"], "diagnostic")
        self.assertEqual(cause["device_class"], "enum")
        # As allocated in #14, plus one per safe-mode reason (#104); "unknown"
        # is left out, as HA takes it as no value.
        self.assertCountEqual(cause["options"], [
            "power_on", "reset_pin", "watchdog", "software", "deep_sleep_alarm",
            "brownout", "other_safe_mode", "mqtt_escalation", "restart_loop", "other",
            "safe_mode_flash_write_fail", "safe_mode_gc_alloc_outside_vm",
            "safe_mode_hard_fault", "safe_mode_interrupt_error", "safe_mode_nlr_jump_fail",
            "safe_mode_no_heap", "safe_mode_programmatic", "safe_mode_sdk_fatal_error",
            "safe_mode_stack_overflow", "safe_mode_watchdog"])
        self.assertEqual(cause["state_topic"], "blinds_f412fa448000/reset_cause/state")

    def test_servo_min_voltage_is_a_diagnostic_voltage_measurement(self):
        # A sensor of its own, so HA graphs it and keeps long-term statistics.
        voltage = json.loads(blinds_payload())["cmps"]["blinds_f412fa448000_servo_min_voltage"]

        self.assertEqual(voltage["p"], "sensor")
        self.assertEqual(voltage["name"], "Servo min voltage")
        self.assertEqual(voltage["entity_category"], "diagnostic")
        self.assertEqual(voltage["device_class"], "voltage")
        self.assertEqual(voltage["unit_of_measurement"], "V")
        self.assertEqual(voltage["state_class"], "measurement")
        self.assertEqual(voltage["suggested_display_precision"], 1)
        self.assertEqual(voltage["state_topic"], "blinds_f412fa448000/servo_min_voltage/state")

    def test_servo_temperatures_are_diagnostic_temperatures_read_from_servo_health(self):
        # Sensors of their own, so HA graphs them and keeps long-term
        # statistics. They read servo_health's message, so they add no topics.
        components = json.loads(blinds_payload())["cmps"]

        for servo in ("lift", "tilt"):
            with self.subTest(servo=servo):
                temperature = components[f"blinds_f412fa448000_{servo}_temperature"]

                self.assertEqual(temperature["p"], "sensor")
                self.assertEqual(temperature["name"], f"{servo.capitalize()} temperature")
                self.assertEqual(temperature["entity_category"], "diagnostic")
                self.assertEqual(temperature["device_class"], "temperature")
                self.assertEqual(temperature["unit_of_measurement"], "°C")
                self.assertEqual(temperature["state_class"], "measurement")
                self.assertEqual(temperature["state_topic"], "blinds_f412fa448000/servo_health/state")
                self.assertEqual(temperature["value_template"],
                                 f"{{{{ value_json.{servo}.temperature }}}}")


if __name__ == "__main__":
    unittest.main()
