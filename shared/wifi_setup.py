"""What the radio needs set before it connects.

The hostname is the device's name on the network: what the router's client
list shows, and what mDNS answers to. CircuitPython picks one itself when
nothing sets it, and which one has changed: through 9.1.x each board derives
its own from the chip, such as cpy-4fa480, but from 9.2.0 every Espressif
board defaults to the ESP-IDF name, "espressif". Five devices answering to
one name is worse than five cryptic ones, so each sets its own (#128).

settings.toml's hostname is read on every CircuitPython the fleet runs.
CIRCUITPY_WIFI_HOSTNAME does the same at the firmware level, but only from
10.3.0, so it's no use while the fleet is mid-upgrade.
"""


def apply_hostname(radio, name):
    """Name the radio before it connects, if settings.toml asks for one.

    Returns the name set, or None when the setting is empty and
    CircuitPython's own choice stands. Setting it after the connect would
    take until the next one to show up, so this belongs before it.
    """
    if not name:
        return None
    radio.hostname = name
    return name
