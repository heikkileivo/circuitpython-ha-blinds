import json
import wifi


class HADiscovery:
    def __init__(self, device_name, device_model, device_id_prefix):
        mac = wifi.radio.mac_address
        self._device_id = device_id_prefix + "_" + "".join(f"{b:02x}" for b in mac)
        self._device_name = device_name
        self._device_model = device_model
        self._components = {}

    @property
    def device_id(self):
        return self._device_id

    def topic(self, entity, suffix):
        return f"{self._device_id}/{entity}/{suffix}"

    def add_component(self, key, platform, config):
        did = self._device_id
        uid = f"{did}_{key}"
        entry = {
            "p": platform,
            "unique_id": uid,
        }
        # Auto-fill state_topic unless explicitly provided
        if "state_topic" not in config:
            entry["state_topic"] = self.topic(key, "state")
        # Handle command_topic: True means auto-generate
        if config.get("command_topic") is True:
            config = dict(config)
            config["command_topic"] = self.topic(key, "set")
        entry.update(config)
        self._components[uid] = entry

    def command_topics(self):
        topics = []
        for entry in self._components.values():
            ct = entry.get("command_topic")
            if ct:
                topics.append(ct)
            # Also check tilt_command_topic for cover entities
            tct = entry.get("tilt_command_topic")
            if tct:
                topics.append(tct)
        return topics

    @property
    def discovery_topic(self):
        return f"homeassistant/device/{self._device_id}/config"

    def discovery_payload_json(self):
        payload = {
            "dev": {
                "ids": [self._device_id],
                "name": self._device_name,
                "manufacturer": "DIY",
                "model": self._device_model,
            },
            "o": {
                "name": "circuitpython-ha-devices",
            },
            "cmps": self._components,
        }
        return json.dumps(payload)
