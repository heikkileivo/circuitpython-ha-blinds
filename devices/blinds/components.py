from discovery import HADiscovery
import servo_health


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
    # One retained JSON message: the health for the state, and each servo's
    # figures for the attributes.
    disc.add_component("servo_health", "sensor", {
        "name": "Servo health",
        "entity_category": "diagnostic",
        "device_class": "enum",
        "options": list(servo_health.HEALTHS),
        "value_template": "{{ value_json.health }}",
        "json_attributes_topic": disc.topic("servo_health", "state"),
    })
    # The lowest supply voltage either servo reported during the last move.
    # Also in servo_health's attributes, but a sensor of its own gets graphs
    # and long-term statistics.
    disc.add_component("servo_min_voltage", "sensor", {
        "name": "Servo min voltage",
        "entity_category": "diagnostic",
        "device_class": "voltage",
        "unit_of_measurement": "V",
        "state_class": "measurement",
        "suggested_display_precision": 1,
    })
    # Dropped entities. The removals stay in every payload for good, so they
    # take effect whichever blind boots first and after any rollback.
    disc.remove_component("uptime", "sensor")
    disc.remove_component("status_led", "switch")
