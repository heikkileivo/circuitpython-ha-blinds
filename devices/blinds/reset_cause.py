"""The reset cause: why the blind last restarted, as published to Home
Assistant.

A restart the firmware triggers itself stores its cause in NVM, then resets
through microcontroller.reset(): the magic, then the cause, in the two bytes
at NVM_OFFSET. NVM survives every reset, so the boot takes a stored cause only
after a software reset, and clears it.

Not sleep memory and a deep sleep: CircuitPython 9.1.1 fakes every deep
sleep on the blinds, because it reads BLE serial as always connected, and
ESP-IDF's bootloader wipes sleep memory on a software reset (#82).

The boot decision is pure, so the host tests run it. The functions that
touch the chip import microcontroller themselves, so this module imports on
CPython too.
"""

MAGIC = 0xB1

# Where the stored cause's two bytes sit in microcontroller.nvm: right after
# the 12-byte travel record at offset 0 (#13).
NVM_OFFSET = 12

# The firmware-triggered causes, by the code in the record's second byte. A
# watchdog reset has no restart of its own, but it keeps "watchdog" through
# the one restart that brings the web workflow up.
BROWNOUT = 1
OTHER_SAFE_MODE = 2
MQTT_ESCALATION = 3
RESTART_LOOP = 4
WATCHDOG = 5

# A safe-mode restart carries its reason, by the name of its
# supervisor.SafeModeReason member, and publishes "safe_mode_<reason>". A
# stored code outlives a deploy, so none may change. BROWNOUT has its own
# cause above, and any reason not here is OTHER_SAFE_MODE. The internal
# WATCHDOG is CircuitPython's, not the chip's watchdog reset.
SAFE_MODE = {"FLASH_WRITE_FAIL": 6,
             "GC_ALLOC_OUTSIDE_VM": 7,
             "HARD_FAULT": 8,
             "INTERRUPT_ERROR": 9,
             "NLR_JUMP_FAIL": 10,
             "NO_HEAP": 11,
             "PROGRAMMATIC": 12,
             "SDK_FATAL_ERROR": 13,
             "STACK_OVERFLOW": 14,
             "WATCHDOG": 15}

_STORED_CAUSES = {BROWNOUT: "brownout",
                  OTHER_SAFE_MODE: "other_safe_mode",
                  MQTT_ESCALATION: "mqtt_escalation",
                  RESTART_LOOP: "restart_loop",
                  WATCHDOG: "watchdog"}
_STORED_CAUSES.update({code: "safe_mode_" + reason.lower() for reason, code in SAFE_MODE.items()})

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

# The reset_cause entity's options: every cause the boot can publish.
OPTIONS = tuple(sorted(set(_STORED_CAUSES.values()) | set(_CHIP_CAUSES.values()) | {OTHER}))


def record(cause):
    """The two bytes that store a firmware-triggered cause."""
    return bytes((MAGIC, cause))


def boot_decision(stored, chip_reason):
    """What to do at boot, given the stored cause's two bytes and the name of
    the chip's reset reason. Returns the cause to publish, whether to
    restart first, and the two bytes to write back, or None.

    Every firmware-triggered restart is a software reset, and the bytes
    survive every reset, so a stored cause counts only when the chip says
    SOFTWARE. One found after any other reset is stale, for example from
    power lost between storing it and the reset, and is cleared.
    """
    if stored[0] == MAGIC and chip_reason == "SOFTWARE":
        # Zeroed, so a soft reload doesn't publish it again.
        return _STORED_CAUSES.get(stored[1], OTHER), False, bytes(2)
    if chip_reason == "WATCHDOG":
        # The web workflow doesn't start after a watchdog reset, but it does
        # after a software reset. The follow-up boot finds the cause, so
        # this restarts only once.
        return None, True, record(WATCHDOG)
    # A stale cause is cleared. With none stored, nothing is written: NVM is
    # flash.
    to_write = bytes(2) if stored[0] == MAGIC else None
    return _CHIP_CAUSES.get(chip_reason, OTHER), False, to_write


def at_boot():
    """This boot's reset cause, for publishing once connected. Clears the
    stored cause. After a watchdog reset, it restarts once instead, storing
    "watchdog", and doesn't return."""
    import microcontroller
    chip_reason = _chip_reason()
    cause, restart_first, to_write = boot_decision(
        bytes(microcontroller.nvm[NVM_OFFSET:NVM_OFFSET + 2]), chip_reason)
    if to_write is not None:
        microcontroller.nvm[NVM_OFFSET:NVM_OFFSET + 2] = to_write
    if restart_first:
        print("Watchdog reset: restarting once, so the web workflow starts.")
        microcontroller.reset()
    print(f"Reset cause: {cause} (chip: {chip_reason})")
    return cause


def restart(cause):
    """Restart with a firmware-triggered cause, BROWNOUT to WATCHDOG or one
    of SAFE_MODE's: store it, then reset. Doesn't return, even if storing fails: a restart
    published as "software" beats none, for example staying in safe mode."""
    import microcontroller
    try:
        microcontroller.nvm[NVM_OFFSET:NVM_OFFSET + 2] = record(cause)
    except Exception as e:
        print(f"Failed to store the reset cause: {e!r}")
    microcontroller.reset()


def _chip_reason():
    """The name of the chip's reset reason, such as "WATCHDOG". Compared
    with each member, so it doesn't rely on how an enum value prints."""
    import microcontroller
    reason = microcontroller.cpu.reset_reason
    for name in _CHIP_REASONS:
        if getattr(microcontroller.ResetReason, name, None) == reason:
            return name
    return "UNKNOWN"
