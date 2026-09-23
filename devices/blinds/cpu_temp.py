"""Whether a CPU die temperature is a reading or the sensor's latch bug.

ESP-IDF 5.2.2, which CircuitPython 9.1.1 and 9.1.3 both pin, shares one range index
between the temperature-sensor driver and the Wi-Fi PHY. The PHY can move
that index while `microcontroller.cpu.temperature` reprograms the hardware
range without it, and then every later read is 55.76 °C low: two range steps
of 27.88 °C. The wrong value lands inside the range the stale index claims,
so the driver never re-ranges. A `microcontroller.reset()` does not clear it
either: Left still read 55.76 °C low after one, so the stuck state is not
only in the driver's globals (#127, fixed upstream in ESP-IDF 5.5).

A fixed floor alone would not do. Left latched while idle and read -28 °C,
which any floor catches; the same latch on the hot afternoon this sensor
exists to measure would read about 20 °C, which looks like an ordinary cool
board. So the check is mostly physical: the die is never colder than the air
around it, and the servos measure that air. 55.76 °C is far larger than the
17-23 °C by which the die normally leads the cavity, so a latch fails the
comparison at any ambient.

The decisions are pure, so the host tests run them.
"""

# Below this the reading is the latch, not a temperature: the cavity is a
# window recess indoors. This is the floor for when no cavity reading is to
# be had, such as before the first servo read or when neither servo replies.
MIN_C = 5.0
# Past the sensor's own range; ESP-IDF itself rejects over 125 °C.
MAX_C = 125.0
# How far below the cavity the die may read before it counts as latched. The
# die normally leads the cavity by 17-23 °C, so this only has to allow for a
# board that has just booted cold and for the servos' own 1 °C steps.
MAX_BELOW_CAVITY_C = 10.0


def plausible(temperature, cavity=None):
    """Whether a die temperature can be a real reading, rather than the
    latched sensor. cavity is the air temperature around the board, when one
    is known. None is not a reading."""
    if temperature is None:
        return False
    # NaN fails every comparison, so it is never plausible.
    if not (MIN_C <= temperature <= MAX_C):
        return False
    if cavity is not None and temperature < cavity - MAX_BELOW_CAVITY_C:
        return False
    return True


def cavity(health_message):
    """The air around the board, from a servo-health message: the cooler of
    the two servos, or None when neither replied.

    The cooler one is the better proxy. A servo heats itself, and a failing
    lift runs hotter than the air it sits in, while the tilt beside it is
    limp. Taking the cooler one keeps this from rejecting good readings.
    """
    temperatures = []
    for servo in ("lift", "tilt"):
        reading = health_message.get(servo)
        if reading is not None and reading.get("temperature") is not None:
            temperatures.append(reading["temperature"])
    if not temperatures:
        return None
    return min(temperatures)
