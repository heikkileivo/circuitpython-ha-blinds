#!/usr/bin/env python3
"""Deploy CircuitPython code to HA devices.

Deploys shared/*.py + devices/{type}/*.py to each device (flat, as CircuitPython requires).
Device type is read from the "type" field in devices.json.

The device root is mirrored: after uploading, every other root .py file is
deleted (the backup taken first keeps them). --restore mirrors a backup the
same way. settings.toml, lib/ and other non-.py files are never touched.
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

from mirror import is_root_py, plan_mirror

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


def get_device_listing(device):
    """Return the device root's entries as the web workflow's GET /fs/ lists them."""
    r = requests.get(fs_url(device), auth=auth(device), headers={"Accept": "application/json"}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()["files"]


def list_device_files(device):
    """List .py files on the device root."""
    return [entry["name"] for entry in get_device_listing(device) if is_root_py(entry)]


def download_file(device, filename):
    r = requests.get(fs_url(device, filename), auth=auth(device), timeout=TIMEOUT)
    r.raise_for_status()
    return r.content


def upload_file(device, filename, content):
    r = requests.put(fs_url(device, filename), auth=auth(device), data=content, timeout=TIMEOUT)
    r.raise_for_status()


def delete_file(device, filename):
    r = requests.delete(fs_url(device, filename), auth=auth(device), timeout=TIMEOUT)
    r.raise_for_status()


def backup_device(device):
    """Backup all .py files from device. Returns backup dir path or None on failure."""
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup_dir = BACKUPS_DIR / device["name"] / timestamp

    print(f"  Backing up to {backup_dir.relative_to(SCRIPT_DIR)}")
    py_files = list_device_files(device)
    if not py_files:
        print("  No .py files found on device, skipping backup")
        return backup_dir

    backup_dir.mkdir(parents=True, exist_ok=True)
    for filename in py_files:
        content = download_file(device, filename)
        (backup_dir / filename).write_bytes(content)
        print(f"    Downloaded {filename} ({len(content)} bytes)")

    return backup_dir


def mirror_device(device, files, dry_run):
    """Upload `files` (name -> local path) to the device root, then delete every
    other root .py file there. Returns True if every request succeeded."""
    try:
        uploads, deletions = plan_mirror(list(files), get_device_listing(device))
    except requests.RequestException as e:
        print(f"  Listing device files failed: {e}")
        return False

    if dry_run:
        print(f"  Would upload {len(uploads)} files:")
        for filename in uploads:
            print(f"    {filename} ({files[filename].stat().st_size} bytes)")
        print(f"  Would delete {len(deletions)} files:")
        for filename in deletions:
            print(f"    {filename}")
        return True

    print(f"  Uploading {len(uploads)} files...")
    all_ok = True
    for filename in uploads:
        content = files[filename].read_bytes()
        try:
            upload_file(device, filename, content)
            print(f"    {filename}: OK ({len(content)} bytes)")
        except requests.RequestException as e:
            print(f"    {filename}: FAILED ({e})")
            all_ok = False

    if not deletions:
        return all_ok

    # The old code may still import a file that's about to go, so only
    # delete once the new code is fully on the device.
    if not all_ok:
        print(f"  Skipping {len(deletions)} deletions because an upload failed")
        return False

    print(f"  Deleting {len(deletions)} stale files...")
    for filename in deletions:
        try:
            delete_file(device, filename)
            print(f"    {filename}: DELETED")
        except requests.RequestException as e:
            print(f"    {filename}: FAILED ({e})")
            all_ok = False

    return all_ok


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


def restore_device(device, timestamp, dry_run=False):
    """Mirror a backup to a device. Returns True if every request succeeded."""
    backup_dir = BACKUPS_DIR / device["name"] / timestamp
    if not backup_dir.exists():
        print(f"Error: backup not found at {backup_dir}")
        sys.exit(1)

    print(f"\n[{device['name']}] Restoring backup {timestamp}")

    print("  Checking connectivity...")
    if not check_reachable(device):
        return False

    files = {p.name: p for p in backup_dir.glob("*.py")}
    return mirror_device(device, files, dry_run)


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
            print(f"  {ts}  [{file_names}]")


def main():
    parser = argparse.ArgumentParser(description="Deploy code to CircuitPython HA devices")
    parser.add_argument("device", nargs="?", help="Target device name (default: all)")
    parser.add_argument("--restore", metavar="TIMESTAMP", help="Restore a backup by timestamp")
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

    print(f"Deploying to {len(targets)} device(s): {', '.join(d['name'] for d in targets)}")

    results = {}
    for device in targets:
        results[device["name"]] = deploy_device(device, args.dry_run)

    print("\n--- Summary ---")
    for name, ok in results.items():
        status = "OK" if ok else "FAILED"
        print(f"  {name}: {status}")

    if not all(results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
