"""Work out how to make a device's root match a set of .py files, and a
device directory tree (lib/) match a local one.

Kept free of network code so it can be host-tested without `requests`.
"""

from collections import namedtuple
from pathlib import Path

# Paths to change on the device, in the order to change them. A directory's
# path ends with "/", as the web workflow's /fs/ API wants it.
#   replaced: entries to delete first, because the wanted tree has a file
#             where the device has a directory, or the other way round
#   mkdirs:   directories to create, parents first
#   uploads:  files that are missing on the device or differ
#   stale:    entries that aren't wanted, to delete last
Plan = namedtuple("Plan", "replaced mkdirs uploads stale")


def plan_tree_mirror(wanted, current):
    """Return the Plan that turns the device tree `current` into `wanted`.

    Both map each path in a tree ("/"-separated) to the file's bytes, or to
    None for a directory. Paths are compared case-insensitively, as the
    device's FAT filesystem does; deletions use the device's spelling.
    """
    device_spelling = {path.casefold(): path for path in current}
    on_device = {path.casefold(): content for path, content in current.items()}
    wanted_folded = {path.casefold() for path in wanted}

    # Deleting a directory deletes its contents, so skip anything under one.
    deleted = set()
    replaced, mkdirs, uploads = [], [], []
    for path in sorted(wanted):
        content = wanted[path]
        folded = path.casefold()
        if folded in on_device and (content is None) != (on_device[folded] is None):
            replaced.append(_web_path(device_spelling[folded], on_device[folded]))
            deleted.add(folded)
            del on_device[folded]
        if content is None:
            if folded not in on_device:
                mkdirs.append(path + "/")
        elif on_device.get(folded) != content:
            uploads.append(path)

    stale = []
    for path in sorted(current):
        folded = path.casefold()
        if folded in wanted_folded or any(parent in deleted for parent in _parents(folded)):
            continue
        deleted.add(folded)
        stale.append(_web_path(path, current[path]))
    return Plan(replaced, mkdirs, uploads, stale)


def _web_path(path, content):
    """`path` as the /fs/ API wants it: with a trailing "/" for a directory."""
    return path + "/" if content is None else path


def read_tree(base, path):
    """Read the local directory base/path into a tree for plan_tree_mirror,
    keyed by paths relative to `base`. Empty if base/path doesn't exist."""
    root = Path(base) / path
    if not root.is_dir():
        return {}
    tree = {path: None}
    for entry in sorted(root.rglob("*")):
        tree[entry.relative_to(base).as_posix()] = None if entry.is_dir() else entry.read_bytes()
    return tree


def _parents(path):
    """The directories above `path` within its tree: "a/b/c" -> "a", "a/b"."""
    parts = path.split("/")
    return ["/".join(parts[:i]) for i in range(1, len(parts))]


def plan_mirror(wanted_names, device_listing):
    """Return the Plan that leaves the device root holding exactly
    `wanted_names` as its .py files. Every wanted file is uploaded.

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
    return Plan([], [], sorted(wanted), sorted(stale))


def is_root_py(entry):
    """True for a .py file entry in a GET /fs/ listing, false for a directory."""
    return entry["name"].endswith(".py") and not entry.get("directory", False)
