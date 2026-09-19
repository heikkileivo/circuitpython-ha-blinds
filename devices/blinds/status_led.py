"""What a blind's status LED shows once its main loop runs (#116): dim and
solid while all is well, and a slow blink at full brightness on an
attention condition, the most severe one if several hold. Pure, so the host
tests run it; the status task in code.py only applies it to the pixel."""

from color import Color
import cover_state
import servo_health

# The pixel's brightness for an attention condition: blink.py's, which boot
# keeps using.
FULL = 0.3
# The pixel's brightness while all is well, low enough not to show between
# the window panes in the evening. Tuned on a device.
DIM = 0.04
# A slow blink is this long on, then this long off, in seconds. The status
# task also checks the blind this often, so the LED follows a change within
# about that.
BLINK_S = 1

SOLID = "solid"
BLINK = "blink"


def decision(health, mqtt_connected, state, in_move):
    """What the LED shows, as (colour, SOLID or BLINK, brightness), from the
    servo health, whether MQTT is connected, the cover state, as the
    cover_state values, and whether a move, a tilt-only one included, is
    under way. An attention condition beats a move: servo health no reply
    or error first, then MQTT disconnected, then an unknown cover state."""
    if health != servo_health.OK:
        return Color.RED, BLINK, FULL
    if not mqtt_connected:
        return Color.ORANGE, BLINK, FULL
    if state == cover_state.UNKNOWN:
        return Color.YELLOW, BLINK, FULL
    if in_move:
        return Color.BLUE, SOLID, DIM
    return Color.GREEN, SOLID, DIM
