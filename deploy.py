#!/usr/bin/env python3
"""Deploy CircuitPython code to blind-control devices."""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).parent
DEVICES_FILE = SCRIPT_DIR / "devices.json"
BACKUPS_DIR = SCRIPT_DIR / "backups"
DEPLOY_FILES = ["code.py", "blinds.py", "packet.py", "tinys3.py", "discovery.py"]
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


def list_device_files(device):
    """List .py files on the device root."""
    url = f"http://{device['host']}/fs/"
    r = requests.get(url, auth=auth(device), headers={"Accept": "application/json"}, timeout=TIMEOUT)
    r.raise_for_status()
    files = []
    for entry in r.json()["files"]:
        name = entry.get("name", "")
        if name.endswith(".py") and not entry.get("directory", False):
            files.append(name)
    return files


def download_file(device, filename):
    url = f"http://{device['host']}/fs/{filename}"
    r = requests.get(url, auth=auth(device), timeout=TIMEOUT)
    r.raise_for_status()
    return r.content


def upload_file(device, filename, content):
    url = f"http://{device['host']}/fs/{filename}"
    r = requests.put(url, auth=auth(device), data=content, timeout=TIMEOUT)
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


def deploy_device(device):
    """Deploy local .py files to a device."""
    print(f"\n[{device['name']}] ({device['host']})")

    print("  Checking connectivity...")
    if not check_reachable(device):
        return False

    try:
        backup_device(device)
    except requests.RequestException as e:
        print(f"  Backup failed: {e}")
        return False

    print("  Uploading files...")
    all_ok = True
    for filename in DEPLOY_FILES:
        filepath = SCRIPT_DIR / filename
        if not filepath.exists():
            print(f"    {filename}: SKIPPED (not found locally)")
            continue
        content = filepath.read_bytes()
        try:
            upload_file(device, filename, content)
            print(f"    {filename}: OK ({len(content)} bytes)")
        except requests.RequestException as e:
            print(f"    {filename}: FAILED ({e})")
            all_ok = False

    return all_ok


def restore_device(device, timestamp):
    """Restore a backup to a device."""
    backup_dir = BACKUPS_DIR / device["name"] / timestamp
    if not backup_dir.exists():
        print(f"Error: backup not found at {backup_dir}")
        sys.exit(1)

    print(f"\n[{device['name']}] Restoring backup {timestamp}")

    print("  Checking connectivity...")
    if not check_reachable(device):
        sys.exit(1)

    print("  Uploading backup files...")
    for filepath in sorted(backup_dir.glob("*.py")):
        content = filepath.read_bytes()
        try:
            upload_file(device, filepath.name, content)
            print(f"    {filepath.name}: OK ({len(content)} bytes)")
        except requests.RequestException as e:
            print(f"    {filepath.name}: FAILED ({e})")


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
    parser = argparse.ArgumentParser(description="Deploy code to CircuitPython devices")
    parser.add_argument("device", nargs="?", help="Target device name (default: all)")
    parser.add_argument("--restore", metavar="TIMESTAMP", help="Restore a backup by timestamp")
    parser.add_argument("--list-backups", action="store_true", help="List available backups")
    args = parser.parse_args()

    if args.list_backups:
        list_backups(args.device)
        return

    devices = load_devices()

    if args.restore:
        if not args.device:
            print("Error: --restore requires a device name")
            sys.exit(1)
        device = get_device(devices, args.device)
        restore_device(device, args.restore)
        return

    if args.device:
        targets = [get_device(devices, args.device)]
    else:
        targets = devices

    print(f"Deploying to {len(targets)} device(s): {', '.join(d['name'] for d in targets)}")

    results = {}
    for device in targets:
        results[device["name"]] = deploy_device(device)

    print("\n--- Summary ---")
    for name, ok in results.items():
        status = "OK" if ok else "FAILED"
        print(f"  {name}: {status}")

    if not all(results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
