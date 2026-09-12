import json


class HADiscovery:
    def __init__(self, device_name, device_model, device_id_prefix, mac=None,
                 availability=False):
        """
        availability: when True, the payload carries one availability_topic
        at the device root, shared by every component, with HA's default
        online/offline payloads. The device must then publish those to it.
        """
        if mac is None:
            # Imported here so host tests, which pass a MAC, run on CPython.
            import wifi
            mac = wifi.radio.mac_address
        self._device_id = device_id_prefix + "_" + "".join(f"{b:02x}" for b in mac)
        self._device_name = device_name
        self._device_model = device_model
        self._availability_topic = f"{self._device_id}/availability" if availability else None
        self._components = {}

    @property
    def device_id(self):
        return self._device_id

    @property
    def availability_topic(self):
        """The root availability topic, or None without availability."""
        return self._availability_topic

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

    def remove_component(self, key, platform):
        # HA removes a component whose entry has only its platform. Leaving it
        # out of the payload leaves the entity behind.
        self._components[f"{self._device_id}_{key}"] = {"p": platform}

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
        if self._availability_topic:
            payload["availability_topic"] = self._availability_topic
        return json.dumps(payload, separators=(",", ":"))
