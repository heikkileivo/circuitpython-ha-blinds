import json
import wifi


class HADiscovery:
    def __init__(self, device_name):
        mac = wifi.radio.mac_address
        self._device_id = "blinds_" + "".join(f"{b:02x}" for b in mac)
        self._device_name = device_name

    @property
    def device_id(self):
        return self._device_id

    def _topic(self, entity, suffix):
        return f"blinds/{self._device_id}/{entity}/{suffix}"

    @property
    def cover_state_topic(self):
        return self._topic("cover", "state")

    @property
    def cover_command_topic(self):
        return self._topic("cover", "set")

    @property
    def tilt_state_topic(self):
        return self._topic("tilt", "state")

    @property
    def tilt_command_topic(self):
        return self._topic("tilt", "set")

    @property
    def speed_state_topic(self):
        return self._topic("speed", "state")

    @property
    def speed_command_topic(self):
        return self._topic("speed", "set")

    @property
    def uptime_state_topic(self):
        return self._topic("uptime", "state")

    @property
    def opened_count_state_topic(self):
        return self._topic("opened_count", "state")

    @property
    def uptime_seconds_state_topic(self):
        return self._topic("uptime_seconds", "state")

    @property
    def status_led_state_topic(self):
        return self._topic("status_led", "state")

    @property
    def status_led_command_topic(self):
        return self._topic("status_led", "set")

    @property
    def discovery_topic(self):
        return f"homeassistant/device/{self._device_id}/config"

    def command_topics(self):
        return [
            self.cover_command_topic,
            self.tilt_command_topic,
            self.speed_command_topic,
            self.status_led_command_topic,
        ]

    def discovery_payload_json(self):
        did = self._device_id
        payload = {
            "dev": {
                "ids": [did],
                "name": self._device_name,
                "manufacturer": "DIY",
                "model": "CircuitPython Blinds",
            },
            "o": {
                "name": "circuitpython-ha-blinds",
            },
            "cmps": {
                f"{did}_cover": {
                    "p": "cover",
                    "device_class": "blind",
                    "name": None,
                    "state_topic": self.cover_state_topic,
                    "command_topic": self.cover_command_topic,
                    "tilt_status_topic": self.tilt_state_topic,
                    "tilt_command_topic": self.tilt_command_topic,
                    "tilt_min": 0,
                    "tilt_max": 100,
                    "payload_open": "OPEN",
                    "payload_close": "CLOSE",
                    "payload_stop": "STOP",
                    "unique_id": f"{did}_cover",
                },
                f"{did}_speed": {
                    "p": "number",
                    "name": "Speed",
                    "state_topic": self.speed_state_topic,
                    "command_topic": self.speed_command_topic,
                    "min": 0,
                    "max": 1023,
                    "step": 1,
                    "unique_id": f"{did}_speed",
                },
                f"{did}_uptime": {
                    "p": "sensor",
                    "name": "Uptime",
                    "state_topic": self.uptime_state_topic,
                    "entity_category": "diagnostic",
                    "unique_id": f"{did}_uptime",
                },
                f"{did}_opened_count": {
                    "p": "sensor",
                    "name": "Opened count",
                    "state_topic": self.opened_count_state_topic,
                    "entity_category": "diagnostic",
                    "state_class": "total_increasing",
                    "unique_id": f"{did}_opened_count",
                },
                f"{did}_uptime_seconds": {
                    "p": "sensor",
                    "name": "Uptime seconds",
                    "state_topic": self.uptime_seconds_state_topic,
                    "entity_category": "diagnostic",
                    "device_class": "duration",
                    "unit_of_measurement": "s",
                    "state_class": "total_increasing",
                    "unique_id": f"{did}_uptime_seconds",
                },
                f"{did}_status_led": {
                    "p": "switch",
                    "name": "Status LED",
                    "state_topic": self.status_led_state_topic,
                    "command_topic": self.status_led_command_topic,
                    "entity_category": "config",
                    "unique_id": f"{did}_status_led",
                },
            },
        }
        return json.dumps(payload)
