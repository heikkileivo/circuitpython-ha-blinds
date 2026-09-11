#!/usr/bin/env python3
"""Drive a device's REPL over the web-workflow serial websocket.

    repl.py <device-name> --interrupt          stop code.py and enter the REPL
    repl.py <device-name> "code"               run code in paste mode, print output
    repl.py <device-name> --reload             Ctrl-D: restart code.py
    repl.py <device-name> --put <file>         copy a file to the device root
"""
import argparse
import asyncio
import base64
import json
import re
import sys
from pathlib import Path

import requests
import websockets

DEVICES_FILE = Path(__file__).resolve().parents[2] / "devices.json"
ANSI = re.compile(r"\x1b\][^\x1b]*\x1b\\|\x1b\[[0-9;]*[A-Za-z]")


def device(name):
    for d in json.loads(DEVICES_FILE.read_text())["devices"]:
        if d["name"] == name or d["host"] == name:
            return d
    sys.exit(f"unknown device {name}")


async def session(dev, sends, timeout, until_prompt=True):
    tok = base64.b64encode(f":{dev['password']}".encode()).decode()
    url = f"ws://{dev['host']}/cp/serial/"
    out = ""
    async with websockets.connect(url, additional_headers={"Authorization": f"Basic {tok}"},
                                  open_timeout=5) as ws:
        for s, pause in sends:
            await ws.send(s)
            await asyncio.sleep(pause)
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while loop.time() < end:
            try:
                m = await asyncio.wait_for(ws.recv(), timeout=end - loop.time())
            except asyncio.TimeoutError:
                break
            chunk = m if isinstance(m, str) else m.decode(errors="replace")
            out += chunk
            sys.stdout.write(ANSI.sub("", chunk).replace("\r", ""))
            sys.stdout.flush()
            if until_prompt and out.endswith(">>> "):
                break
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("device")
    p.add_argument("code", nargs="?")
    p.add_argument("--interrupt", action="store_true")
    p.add_argument("--reload", action="store_true")
    p.add_argument("--put")
    p.add_argument("--timeout", type=float, default=20)
    a = p.parse_args()
    dev = device(a.device)
    if a.put:
        path = Path(a.put)
        r = requests.put(f"http://{dev['host']}/fs/{path.name}", auth=("", dev["password"]),
                         data=path.read_bytes(), timeout=10)
        print(r.status_code, r.reason)
        return
    if a.interrupt:
        sends = [("\x03", 0.3), ("\x03", 0.5), ("\r", 0.3)]
    elif a.reload:
        asyncio.run(session(dev, [("\x04", 0.1)], a.timeout, until_prompt=False))
        return
    else:
        code = a.code.replace("\n", "\r")
        sends = [("\x05", 0.2), (code, 0.1), ("\x04", 0)]
    asyncio.run(session(dev, sends, a.timeout))
    print()


if __name__ == "__main__":
    main()
