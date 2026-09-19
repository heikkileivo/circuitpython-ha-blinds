#!/usr/bin/env python3
"""Drive a device's REPL over the web-workflow serial websocket.

    repl.py <device-name> --interrupt          stop code.py and enter the REPL
    repl.py <device-name> "code"               run code in paste mode, print output
    repl.py <device-name> --reload             Ctrl-D: restart code.py
    repl.py <device-name> --put <file>         copy a file to the device root

Moved from tools/bench/repl.py on the bench/scs-servo-bench branch, as the
transport tools/onboard.py uses (#113). Running code exits with status 1 if
its output has a traceback.

websockets (14 or later) and requests are imported where they're used, so
the host tests import this module without them.
"""
import argparse
import asyncio
import base64
import json
import re
import sys
from pathlib import Path

DEVICES_FILE = Path(__file__).resolve().parents[1] / "devices.json"
ANSI = re.compile(r"\x1b\][^\x1b]*\x1b\\|\x1b\[[0-9;]*[A-Za-z]")
PROMPT = ">>> "
TRACEBACK = "Traceback (most recent call last):"

# Ctrl-C twice stops code.py, and Enter gets past "Press any key to enter
# the REPL". Ctrl-C also releases the watchdog.
INTERRUPT = [("\x03", 0.3), ("\x03", 0.5), ("\r", 0.3)]


def device(name):
    """A device from devices.json, by its name or host."""
    for d in json.loads(DEVICES_FILE.read_text())["devices"]:
        if d["name"] == name or d["host"] == name:
            return d
    sys.exit(f"unknown device {name}")


def clean(output):
    """REPL output without its terminal escapes and carriage returns."""
    return ANSI.sub("", output).replace("\r", "")


def has_traceback(output):
    return TRACEBACK in output


def at_prompt(output):
    """Whether the output ends at the REPL's prompt: the device is done."""
    return clean(output).endswith(PROMPT)


def paste(code):
    """The sends that run code in paste mode: Ctrl-E, the code, Ctrl-D."""
    return [("\x05", 0.2), (code.replace("\n", "\r"), 0.1), ("\x04", 0)]


async def session(dev, sends, timeout, until_prompt=True, echo=True):
    """Send each of sends, then read what comes back until the prompt, or
    for timeout seconds. Returns the raw output."""
    import websockets

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
            if echo:
                sys.stdout.write(clean(chunk))
                sys.stdout.flush()
            if until_prompt and at_prompt(out):
                break
    return out


class Repl:
    """One device's REPL. Each call opens its own websocket session, so
    nothing stays connected while the operator works on the blind."""

    def __init__(self, dev, echo=True):
        self.dev = dev
        self.echo = echo

    def interrupt(self, timeout=10):
        """Stop code.py and enter the REPL. Returns the output."""
        return asyncio.run(session(self.dev, INTERRUPT, timeout, echo=self.echo))

    def run(self, code, timeout=20):
        """Run code in paste mode. Returns the output, up to the prompt, or
        what came within timeout seconds."""
        return asyncio.run(session(self.dev, paste(code), timeout, echo=self.echo))

    def reload(self, timeout=20):
        """Ctrl-D: soft-reboot, which runs code.py again."""
        return asyncio.run(session(self.dev, [("\x04", 0.1)], timeout, until_prompt=False,
                                   echo=self.echo))


def main():
    p = argparse.ArgumentParser(description="Drive a device's REPL over the web-workflow "
                                            "serial websocket.")
    p.add_argument("device")
    p.add_argument("code", nargs="?")
    p.add_argument("--interrupt", action="store_true")
    p.add_argument("--reload", action="store_true")
    p.add_argument("--put")
    p.add_argument("--timeout", type=float, default=20)
    a = p.parse_args()
    dev = device(a.device)
    repl = Repl(dev)
    if a.put:
        import requests

        path = Path(a.put)
        r = requests.put(f"http://{dev['host']}/fs/{path.name}", auth=("", dev["password"]),
                         data=path.read_bytes(), timeout=10)
        print(r.status_code, r.reason)
        return
    if a.interrupt:
        repl.interrupt(a.timeout)
    elif a.reload:
        repl.reload(a.timeout)
        return
    else:
        out = repl.run(a.code, a.timeout)
        print()
        if has_traceback(out):
            sys.exit(1)
    print()


if __name__ == "__main__":
    main()
