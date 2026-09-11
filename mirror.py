"""Work out how to make a device's root match a set of .py files.

Kept free of network code so it can be host-tested without `requests`.
"""


def plan_mirror(wanted_names, device_listing):
    """Return (uploads, deletions) that leave the device root holding exactly
    `wanted_names` as its .py files.

    `device_listing` is the entry list from the web workflow's GET /fs/.
    Directories and non-.py files are never deleted. Names are compared
    case-insensitively, as the device's FAT filesystem does.
    """
    wanted = set(wanted_names)
    wanted_folded = {name.casefold() for name in wanted}
    stale = [
        entry["name"]
        for entry in device_listing
        if is_root_py(entry) and entry["name"].casefold() not in wanted_folded
    ]
    return sorted(wanted), sorted(stale)


def is_root_py(entry):
    """True for a .py file entry in a GET /fs/ listing, false for a directory."""
    return entry["name"].endswith(".py") and not entry.get("directory", False)
