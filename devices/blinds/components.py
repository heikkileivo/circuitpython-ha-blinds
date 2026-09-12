from discovery import HADiscovery


def blinds_discovery(device_name, mac=None):
    """The blinds' HADiscovery, with root availability and every component."""
    disc = HADiscovery(device_name, "CircuitPython Blinds", "blinds", mac=mac,
                       availability=True)
    add_components(disc)
    return disc


def add_components(disc):
    """Add the blinds' Home Assistant components to an HADiscovery."""
    disc.add_component("cover", "cover", {
        "device_class": "blind",
        "name": None,
        "command_topic": True,
        "tilt_status_topic": disc.topic("tilt", "state"),
        "tilt_command_topic": disc.topic("tilt", "set"),
        "tilt_min": 0,
        "tilt_max": 100,
        "payload_open": "OPEN",
        "payload_close": "CLOSE",
        "payload_stop": "STOP",
    })
    disc.add_component("speed", "number", {
        "name": "Speed",
        "command_topic": True,
        "min": 0,
        "max": 1023,
        "step": 1,
    })
    disc.add_component("opened_count", "sensor", {
        "name": "Opened count",
        "entity_category": "diagnostic",
        "state_class": "total_increasing",
    })
    disc.add_component("uptime_seconds", "sensor", {
        "name": "Uptime seconds",
        "entity_category": "diagnostic",
        "device_class": "duration",
        "unit_of_measurement": "s",
        "state_class": "total_increasing",
    })
    disc.add_component("reconnects", "sensor", {
        "name": "Reconnects",
        "entity_category": "diagnostic",
        "state_class": "measurement",
    })
    # Dropped entities. The removals stay in every payload for good, so they
    # take effect whichever blind boots first and after any rollback.
    disc.remove_component("uptime", "sensor")
    disc.remove_component("status_led", "switch")
