"""The reset cause: why the blind last restarted, as published to Home
Assistant.

A restart the firmware triggers itself stores its cause in sleep memory,
which a deep sleep keeps and every real reset wipes: offset 0 holds the
magic, and offset 1 the cause. It restarts through a short deep sleep,
never microcontroller.reset(), which would hide the cause behind a software
reset.

The boot decision is pure, so the host tests run it. The functions that
touch the chip import alarm, microcontroller and time themselves, so this
module imports on CPython too.
"""

MAGIC = 0xB1

# The firmware-triggered causes, by the code stored at offset 1. A watchdog
# reset has no restart of its own, but it keeps "watchdog" through the one
# restart that brings the web workflow up.
BROWNOUT = 1
OTHER_SAFE_MODE = 2
MQTT_ESCALATION = 3
RESTART_LOOP = 4
WATCHDOG = 5

_STORED_CAUSES = {BROWNOUT: "brownout",
                  OTHER_SAFE_MODE: "other_safe_mode",
                  MQTT_ESCALATION: "mqtt_escalation",
                  RESTART_LOOP: "restart_loop",
                  WATCHDOG: "watchdog"}

# The cause for each of the chip's reasons, by the name of its
# microcontroller.ResetReason member. UNKNOWN and RESCUE_DEBUG are "other",
# because HA takes "unknown" as no value.
_CHIP_CAUSES = {"POWER_ON": "power_on",
                "RESET_PIN": "reset_pin",
                "WATCHDOG": "watchdog",
                "SOFTWARE": "software",
                "DEEP_SLEEP_ALARM": "deep_sleep_alarm",
                "BROWNOUT": "brownout"}
OTHER = "other"
# Every member of microcontroller.ResetReason in CircuitPython 9.1.
_CHIP_REASONS = tuple(_CHIP_CAUSES) + ("UNKNOWN", "RESCUE_DEBUG")

# How long the one restart after a watchdog reset sleeps.
WATCHDOG_RESTART_S = 1

# The reset_cause entity's options.
OPTIONS = ("power_on", "reset_pin", "watchdog", "software", "deep_sleep_alarm",
           "brownout", "other_safe_mode", "mqtt_escalation", "restart_loop", OTHER)


def record(cause):
    """The two sleep-memory bytes that store a firmware-triggered cause."""
    return bytes((MAGIC, cause))


def boot_decision(stored, chip_reason):
    """What to do at boot, given sleep memory's first two bytes and the name
    of the chip's reset reason. Returns the cause to publish, whether to
    restart first, and the two bytes to write to sleep memory, or None.

    Only the magic is checked, not the chip's reason as well: with USB
    connected the deep sleep is faked without a reset, and the chip keeps
    its earlier reason. Every real reset wipes sleep memory, so the magic
    alone is enough.
    """
    if stored[0] == MAGIC:
        # Zeroed, so a soft reload doesn't publish it again.
        return _STORED_CAUSES.get(stored[1], OTHER), False, bytes(2)
    if chip_reason == "WATCHDOG":
        # The web workflow doesn't start after a watchdog reset, but it does
        # after a deep-sleep alarm. The follow-up boot finds the magic, so
        # this restarts only once.
        return None, True, record(WATCHDOG)
    return _CHIP_CAUSES.get(chip_reason, OTHER), False, None


def at_boot():
    """This boot's reset cause, for publishing once connected. Clears the
    stored cause. After a watchdog reset with none stored, it restarts once
    instead, keeping "watchdog", and doesn't return."""
    import alarm
    chip_reason = _chip_reason()
    cause, restart_first, written = boot_decision(bytes(alarm.sleep_memory[0:2]), chip_reason)
    if written is not None:
        alarm.sleep_memory[0:2] = written
    if restart_first:
        print("Watchdog reset: restarting once, so the web workflow starts.")
        _deep_sleep(WATCHDOG_RESTART_S)
    print(f"Reset cause: {cause} (chip: {chip_reason})")
    return cause


def restart(cause, seconds):
    """Restart with a firmware-triggered cause, BROWNOUT to WATCHDOG: store
    it, then deep-sleep for the given seconds. Doesn't return."""
    import alarm
    alarm.sleep_memory[0:2] = record(cause)
    _deep_sleep(seconds)


def _deep_sleep(seconds):
    import alarm
    import time
    alarm.exit_and_deep_sleep_until_alarms(
        alarm.time.TimeAlarm(monotonic_time=time.monotonic() + seconds))


def _chip_reason():
    """The name of the chip's reset reason, such as "WATCHDOG". Compared
    with each member, so it doesn't rely on how an enum value prints."""
    import microcontroller
    reason = microcontroller.cpu.reset_reason
    for name in _CHIP_REASONS:
        if getattr(microcontroller.ResetReason, name, None) == reason:
            return name
    return "UNKNOWN"
