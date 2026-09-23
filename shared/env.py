"""Reads of settings.toml, one function per type the code wants.

os.getenv() doesn't return the same type on every CircuitPython. Through 9.x
it parses a TOML integer into an int and hands back everything else as a str.
From 10.2.0 on it returns a str for every value, and supervisor.get_setting()
is the one that keeps the TOML type (#128).

A default keeps its own type either way, so os.getenv("default_speed", 800)
is an int on a device that doesn't set default_speed and a str on one that
does. The three blinds each have their own settings.toml, so the same line
can work on one blind and fail on the next.

So no code reads settings.toml directly: it asks for the type it wants, and
gets that type whatever CircuitPython returned. A key that isn't set gives
the default back as it was passed, so a default of None still reads as None.

supervisor.get_setting() would do the conversion instead, but it arrived in
10.2.0, so it can't be shared across a fleet that's mid-upgrade, and it gives
the host tests nothing to call.
"""

import os


def integer(key, default=None):
    """The setting as an int, or default when it isn't set."""
    value = os.getenv(key)
    if value is None:
        return default
    return int(value)


def number(key, default=None):
    """The setting as a float, or default when it isn't set."""
    value = os.getenv(key)
    if value is None:
        return default
    return float(value)


def text(key, default=None):
    """The setting as a str, or default when it isn't set."""
    value = os.getenv(key)
    if value is None:
        return default
    return str(value)
