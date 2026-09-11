#!/usr/bin/env python3
"""Deploy CircuitPython code to HA devices.

Deploys shared/*.py + devices/{type}/*.py to each device (flat, as CircuitPython requires).
Device type is read from the "type" field in devices.json.

The device root is mirrored: after uploading, every other root .py file is
deleted (the backup taken first keeps them). A plain deploy never touches
settings.toml, lib/ or other non-.py files.

--libs mirrors the repo's lib/ to the device's lib/ instead. It backs up the
root .py files and the whole lib/ first, then uploads what's missing or
differs and deletes what the repo doesn't have.

--restore mirrors a backup the same way, lib/ included when the backup holds one.
--dry-run prints the uploads and deletions without making them.
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import requests

from mirror import is_root_py, plan_mirror, plan_tree_mirror, read_tree

SCRIPT_DIR = Path(__file__).parent
DEVICES_FILE = SCRIPT_DIR / "devices.json"
BACKUPS_DIR = SCRIPT_DIR / "backups"
SHARED_DIR = SCRIPT_DIR / "shared"
DEVICES_DIR = SCRIPT_DIR / "devices"
TIMEOUT = 10


def load_devices():
    if not DEVICES_FILE.exists():
        print(f"Error: {DEVICES_FILE} not found. Copy devices.json.example and fill in your values.")
        sys.exit(1)
    with open(DEVICES_FILE) as f:
        return json.load(f)["devices"]


def get_device(devices, name):
    for d in devices:
        if d["name"] == name:
            return d
    print(f"Error: device '{name}' not found in {DEVICES_FILE}")
    print(f"Available devices: {', '.join(d['name'] for d in devices)}")
    sys.exit(1)


def get_deploy_files(device_type):
    """Collect shared/*.py + devices/{type}/*.py files to deploy."""
    files = {}

    # Shared files
    for py in sorted(SHARED_DIR.glob("*.py")):
        files[py.name] = py

    # Device-specific files (override shared if same name)
    type_dir = DEVICES_DIR / device_type
    if not type_dir.exists():
        print(f"Error: device type directory not found: {type_dir}")
        sys.exit(1)

    for py in sorted(type_dir.glob("*.py")):
        files[py.name] = py

    return files


def auth(device):
    return ("", device["password"])


def check_reachable(device):
    url = f"http://{device['host']}/cp/version.json"
    try:
        r = requests.get(url, auth=auth(device), timeout=TIMEOUT)
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        print(f"  Device unreachable: {e}")
        return False


def fs_url(device, filename=""):
    return f"http://{device['host']}/fs/{quote(filename)}"


def get_device_listing(device, path=""):
    """Return a device directory's entries as the web workflow's GET /fs/ lists
    them. `path` is "" for the root, otherwise it ends with "/"."""
    r = requests.get(fs_url(device, path), auth=auth(device), headers={"Accept": "application/json"}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()["files"]


def download_file(device, filename):
    r = requests.get(fs_url(device, filename), auth=auth(device), timeout=TIMEOUT)
    r.raise_for_status()
    return r.content


def upload_file(device, filename, content):
    r = requests.put(fs_url(device, filename), auth=auth(device), data=content, timeout=TIMEOUT)
    r.raise_for_status()


def make_dir(device, path):
    """Create a directory; `path` ends with "/"."""
    r = requests.put(fs_url(device, path), auth=auth(device), timeout=TIMEOUT)
    r.raise_for_status()


def delete_file(device, filename):
    """Delete a file, or a directory and its contents if `filename` ends with "/"."""
    r = requests.delete(fs_url(device, filename), auth=auth(device), timeout=TIMEOUT)
    r.raise_for_status()


def backup_device(device, with_lib=False):
    """Backup all root .py files from the device, and with `with_lib` its whole
    lib/ too. Returns the backup dir path."""
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup_dir = BACKUPS_DIR / device["name"] / timestamp

    print(f"  Backing up to {backup_dir.relative_to(SCRIPT_DIR)}")
    listing = get_device_listing(device)
    py_files = [entry["name"] for entry in listing if is_root_py(entry)]
    if py_files:
        backup_dir.mkdir(parents=True, exist_ok=True)
    else:
        print("  No .py files found on device")

    for filename in py_files:
        content = download_file(device, filename)
        (backup_dir / filename).write_bytes(content)
        print(f"    Downloaded {filename} ({len(content)} bytes)")

    if with_lib:
        if any(entry["name"].casefold() == "lib" and entry.get("directory", False) for entry in listing):
            download_dir(device, backup_dir, "lib")
        else:
            print("  No lib/ found on device")

    return backup_dir


def download_dir(device, backup_dir, path):
    """Download the device directory `path` into backup_dir/path, recursively."""
    (backup_dir / path).mkdir(parents=True, exist_ok=True)
    for entry in get_device_listing(device, path + "/"):
        child = f"{path}/{entry['name']}"
        if entry.get("directory", False):
            download_dir(device, backup_dir, child)
        else:
            content = download_file(device, child)
            (backup_dir / child).write_bytes(content)
            print(f"    Downloaded {child} ({len(content)} bytes)")


def apply_plan(device, plan, contents, dry_run):
    """Carry out a mirror.Plan on the device, uploading from `contents`
    (device path -> bytes). Returns True if every request succeeded."""
    if dry_run:
        if plan.replaced:
            print(f"  Would delete {len(plan.replaced)} entries the repo has as a file where it's a directory, or the other way round:")
            for path in plan.replaced:
                print(f"    {describe_deletion(path)}")
        if plan.mkdirs:
            print(f"  Would create {len(plan.mkdirs)} directories:")
            for path in plan.mkdirs:
                print(f"    {path}")
        print(f"  Would upload {len(plan.uploads)} files:")
        for path in plan.uploads:
            print(f"    {path} ({len(contents[path])} bytes)")
        print(f"  Would delete {len(plan.stale)} files:")
        for path in plan.stale:
            print(f"    {describe_deletion(path)}")
        return True

    all_ok = True
    if plan.replaced:
        print(f"  Deleting {len(plan.replaced)} entries to replace them...")
        for path in plan.replaced:
            all_ok = try_request(path, lambda: delete_file(device, path), "DELETED") and all_ok

    if plan.mkdirs:
        print(f"  Creating {len(plan.mkdirs)} directories...")
        for path in plan.mkdirs:
            all_ok = try_request(path, lambda: make_dir(device, path), "CREATED") and all_ok

    print(f"  Uploading {len(plan.uploads)} files...")
    for path in plan.uploads:
        content = contents[path]
        all_ok = try_request(path, lambda: upload_file(device, path, content), f"OK ({len(content)} bytes)") and all_ok

    if not plan.stale:
        return all_ok

    # The old code may still import a file that's about to go, so only
    # delete once the new code is fully on the device.
    if not all_ok:
        print(f"  Skipping {len(plan.stale)} deletions because an upload failed")
        return False

    print(f"  Deleting {len(plan.stale)} stale files...")
    for path in plan.stale:
        all_ok = try_request(path, lambda: delete_file(device, path), "DELETED") and all_ok

    return all_ok


def describe_deletion(path):
    return f"{path} (the directory and everything in it)" if path.endswith("/") else path


def try_request(path, request, success_text):
    """Call `request()` and print how it went. Returns True if it succeeded."""
    try:
        request()
        print(f"    {path}: {success_text}")
        return True
    except requests.RequestException as e:
        print(f"    {path}: FAILED ({e})")
        return False


def mirror_device(device, files, dry_run):
    """Upload `files` (name -> local path) to the device root, then delete every
    other root .py file there. Returns True if every request succeeded."""
    try:
        plan = plan_mirror(list(files), get_device_listing(device))
    except requests.RequestException as e:
        print(f"  Listing device files failed: {e}")
        return False

    contents = {name: path.read_bytes() for name, path in files.items()}
    return apply_plan(device, plan, contents, dry_run)


def mirror_lib(device, source_dir, backup_dir, dry_run):
    """Make the device's lib/ match source_dir/lib/, diffing against the copy
    of the device's lib/ in backup_dir. Returns True if every request succeeded."""
    wanted = read_tree(source_dir, "lib")
    plan = plan_tree_mirror(wanted, read_tree(backup_dir, "lib"))
    return apply_plan(device, plan, wanted, dry_run)


def deploy_device(device, dry_run=False):
    """Mirror shared + device-type files to a device."""
    device_type = device.get("type")
    if not device_type:
        print(f"  Error: device '{device['name']}' has no 'type' field")
        return False

    print(f"\n[{device['name']}] type={device_type} ({device['host']})")

    print("  Checking connectivity...")
    if not check_reachable(device):
        return False

    if not dry_run:
        try:
            backup_device(device)
        except requests.RequestException as e:
            print(f"  Backup failed: {e}")
            return False

    return mirror_device(device, get_deploy_files(device_type), dry_run)


def deploy_libs(device, dry_run=False):
    """Back up a device's root .py files and lib/, then mirror the repo's lib/
    to it. The backup is taken even in a dry run, since it only reads."""
    print(f"\n[{device['name']}] lib/ ({device['host']})")

    print("  Checking connectivity...")
    if not check_reachable(device):
        return False

    try:
        backup_dir = backup_device(device, with_lib=True)
    except requests.RequestException as e:
        print(f"  Backup failed: {e}")
        return False

    if not (SCRIPT_DIR / "lib").is_dir():
        print("  The repo has no lib/ to mirror, so nothing is uploaded or deleted")
        # In a dry run that's expected: it's how a device's libraries get pulled.
        return dry_run

    return mirror_lib(device, SCRIPT_DIR, backup_dir, dry_run)


def restore_device(device, timestamp, dry_run=False):
    """Mirror a backup to a device, lib/ included when the backup holds one.
    Returns True if every request succeeded."""
    backup_dir = BACKUPS_DIR / device["name"] / timestamp
    if not backup_dir.exists():
        print(f"Error: backup not found at {backup_dir}")
        sys.exit(1)

    print(f"\n[{device['name']}] Restoring backup {timestamp}")

    print("  Checking connectivity...")
    if not check_reachable(device):
        return False

    restore_lib = (backup_dir / "lib").is_dir()
    if restore_lib:
        # Diffing lib/ needs the device's current copy, and backing it up
        # keeps the restore itself undoable.
        try:
            pre_restore_backup = backup_device(device, with_lib=True)
        except requests.RequestException as e:
            print(f"  Backup failed: {e}")
            return False

    files = {p.name: p for p in backup_dir.glob("*.py")}
    all_ok = mirror_device(device, files, dry_run)
    if restore_lib:
        all_ok = mirror_lib(device, backup_dir, pre_restore_backup, dry_run) and all_ok
    return all_ok


def list_backups(device_name=None):
    """List available backups."""
    if not BACKUPS_DIR.exists():
        print("No backups found.")
        return

    if device_name:
        device_dirs = [BACKUPS_DIR / device_name]
    else:
        device_dirs = sorted(p for p in BACKUPS_DIR.iterdir() if p.is_dir())

    if not device_dirs:
        print("No backups found.")
        return

    for device_dir in device_dirs:
        if not device_dir.exists():
            print(f"{device_dir.name}: no backups")
            continue
        timestamps = sorted(p.name for p in device_dir.iterdir() if p.is_dir())
        if not timestamps:
            continue
        print(f"\n{device_dir.name}:")
        for ts in timestamps:
            files = list((device_dir / ts).glob("*.py"))
            file_names = ", ".join(f.name for f in sorted(files))
            lib = " + lib/" if (device_dir / ts / "lib").is_dir() else ""
            print(f"  {ts}  [{file_names}]{lib}")


def main():
    parser = argparse.ArgumentParser(description="Deploy code to CircuitPython HA devices")
    parser.add_argument("device", nargs="?", help="Target device name (default: all)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--restore", metavar="TIMESTAMP", help="Restore a backup by timestamp, lib/ included when the backup holds one"
    )
    mode.add_argument(
        "--libs", action="store_true", help="Mirror the repo's lib/ to the device's lib/ instead of deploying code"
    )
    parser.add_argument("--list-backups", action="store_true", help="List available backups")
    parser.add_argument(
        "--dry-run", action="store_true", help="Print the uploads and deletions without making them"
    )
    args = parser.parse_args()

    if args.list_backups:
        list_backups(args.device)
        return

    devices = load_devices()

    if args.dry_run:
        print("Dry run: nothing will be uploaded or deleted.")

    if args.restore:
        if not args.device:
            print("Error: --restore requires a device name")
            sys.exit(1)
        device = get_device(devices, args.device)
        if not restore_device(device, args.restore, args.dry_run):
            sys.exit(1)
        return

    if args.device:
        targets = [get_device(devices, args.device)]
    else:
        targets = devices

    names = ", ".join(d["name"] for d in targets)
    if args.libs:
        print(f"Mirroring lib/ to {len(targets)} device(s): {names}")
        deploy = deploy_libs
    else:
        print(f"Deploying to {len(targets)} device(s): {names}")
        deploy = deploy_device

    results = {}
    for device in targets:
        results[device["name"]] = deploy(device, args.dry_run)

    print("\n--- Summary ---")
    for name, ok in results.items():
        status = "OK" if ok else "FAILED"
        print(f"  {name}: {status}")

    if not all(results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
