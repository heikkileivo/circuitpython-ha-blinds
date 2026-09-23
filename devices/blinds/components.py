from discovery import HADiscovery
import reset_cause
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
    # Why the blind last restarted, published once per boot.
    disc.add_component("reset_cause", "sensor", {
        "name": "Reset cause",
        "entity_category": "diagnostic",
        "device_class": "enum",
        "options": list(reset_cause.OPTIONS),
    })
    # One retained JSON message: the health for the state, and each servo's
    # figures for the attributes.
    health_topic = disc.topic("servo_health", "state")
    disc.add_component("servo_health", "sensor", {
        "name": "Servo health",
        "entity_category": "diagnostic",
        "device_class": "enum",
        "options": list(servo_health.HEALTHS),
        "value_template": "{{ value_json.health }}",
        "json_attributes_topic": health_topic,
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
    # Each servo's temperature, read from servo_health's message, so they add
    # no topics. A failing lift runs hot; the tilt, limp in the same cavity,
    # is its reference. A servo that doesn't reply reads null, which HA shows
    # as unknown. The names are spelled out: CircuitPython's str has no
    # capitalize() (#103).
    for servo, name in (("lift", "Lift temperature"), ("tilt", "Tilt temperature")):
        disc.add_component(servo + "_temperature", "sensor", {
            "name": name,
            "entity_category": "diagnostic",
            "device_class": "temperature",
            "unit_of_measurement": "°C",
            "state_class": "measurement",
            "state_topic": health_topic,
            "value_template": "{{ value_json." + servo + ".temperature }}",
        })
    # The chip's own die temperature. The servos' temperatures measure the
    # cavity between the window panes, a few centimetres from the board; this
    # measures the board itself, which is what dies when the sun heats the
    # cavity (#125). The key is short: the payload has little headroom left
    # against #14's budget.
    disc.add_component("cpu_temp", "sensor", {
        "name": "CPU temperature",
        "entity_category": "diagnostic",
        "device_class": "temperature",
        "unit_of_measurement": "°C",
        "state_class": "measurement",
    })
    # Dropped entities. The removals stay in every payload for good, so they
    # take effect whichever blind boots first and after any rollback.
    disc.remove_component("uptime", "sensor")
    disc.remove_component("status_led", "switch")
