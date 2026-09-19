#!/usr/bin/env python3
"""Onboard a new servo into a blind, as its lift or its tilt (#99, #113).

    onboard.py <device-name> <lift|tilt>

Walks the operator through docs/servo-onboarding.md's run, driving the
device's onboard module over the web-workflow serial websocket (repl.py),
and nothing else on the device's port 80. Ctrl-C stops code.py first, so
nothing drives the servo meanwhile.

Each step on the device prints marker lines, "ONBOARD " then JSON, which
this reads. A failed step, a refusal, a traceback or a timeout stops the
tool there and leaves the blind at the REPL. Running the tool again is the
recovery.
"""
import argparse
import json
import sys

from repl import Repl, at_prompt, clean, device, has_traceback

MARKER = "ONBOARD "


def markers(output):
    """The markers in REPL output, in order. Paste mode's echo of the code
    starts with "=== ", so it never reads as one."""
    return [json.loads(line[len(MARKER):]) for line in clean(output).split("\n")
            if line.startswith(MARKER)]


def outcome(output, last_step):
    """Whether a call on the device went through, and why not: it reached
    the prompt with no traceback, every marker went through, and the last
    one is last_step's. Returns (ok, reason), with reason None if ok."""
    if has_traceback(output):
        return False, "the device raised an error"
    if not at_prompt(output):
        return False, "no reply in time"
    found = markers(output)
    for marker in found:
        if not marker["ok"]:
            return False, f"the {marker['step']} step failed"
    if not found or found[-1]["step"] != last_step:
        return False, f"no {last_step} marker"
    return True, None


def step_code(call):
    """The code that runs one call on the device's Onboarding. It opens the
    servo bus and closes it again, so a rerun after a failure finds the
    pins free."""
    return ("import onboard\n"
            "o = onboard.on_device()\n"
            "try:\n"
            f"    o.{call}\n"
            "finally:\n"
            "    o.close()\n")


def describe(marker):
    """One marker as a line for the operator."""
    values = [f"{name.replace('_', ' ')} {_value(value)}" for name, value in marker.items()
              if name not in ("step", "ok")]
    line = f"{marker['step']}: {'ok' if marker['ok'] else 'FAILED'}"
    return line + (f" ({'; '.join(values)})" if values else "")


def _value(value):
    if isinstance(value, list) and value and all(isinstance(v, dict) and "id" in v
                                                 for v in value):
        return ", ".join(f"ID {v['id']} at {v['baud_rate']} baud"
                         + (f" ({v['problem']})" if "problem" in v else "") for v in value)
    if value == []:
        return "none"
    return json.dumps(value)


def run(name, role, repl, confirm=input, say=print, reload_timeout=5):
    """Onboard a new servo into the blind name as role, "lift" or "tilt",
    on its REPL repl. confirm asks the operator to do something and waits,
    and say tells them. Returns whether it passed."""
    other = "tilt" if role == "lift" else "lift"
    say("Stopping code.py...")
    if not at_prompt(repl.interrupt()):
        return _stopped(name, say, "the blind didn't reach the REPL")

    confirm(f"Unplug the {other} servo and the old {role} servo from the daisy chain, and "
            f"plug in the new {role} servo, so it's the only servo on the bus. "
            "Press Enter when it is. ")
    if not _step(name, repl, say, f'onboard("{role}")', "lock_on", 90):
        return False

    confirm(f"Unplug the new {role} servo and plug it back in, to power-cycle it. "
            "Press Enter when it's back. ")
    if not _step(name, repl, say, f'verify("{role}")', "verify", 60):
        return False
    if role == "lift" and not _step(name, repl, say, 'forget_travel("lift")',
                                    "forget_travel", 20):
        return False

    confirm(f"Plug the {other} servo back into the daisy chain. Press Enter when it is. ")
    if not _step(name, repl, say, "bus_check()", "bus_check", 60):
        return False

    say("Soft-rebooting the blind...")
    repl.reload(reload_timeout)
    say(f"PASS: the new {role} servo is onboarded, and the blind is running code.py again. "
        "See docs/servo-onboarding.md for the checks after it.")
    return True


def _step(name, repl, say, call, last_step, timeout):
    """Run call on the device and tell the operator its markers. Returns
    whether it went through."""
    say(f"Running {call} on the blind...")
    output = repl.run(step_code(call), timeout)
    for marker in markers(output):
        say("  " + describe(marker))
    ok, reason = outcome(output, last_step)
    if not ok:
        if has_traceback(output):
            say(clean(output))
        return _stopped(name, say, reason)
    return True


def _stopped(name, say, reason):
    say(f"FAIL: {reason}. The blind is left at the REPL, not rebooted.\n"
        f"Run the tool again to recover: its scan finds the servo wherever it was left.\n"
        f"To get the blind running without onboarding, once its servos are right: "
        f"python3 tools/repl.py {name} --reload, or power-cycle it.")
    return False


def main():
    parser = argparse.ArgumentParser(
        description="Onboard a new servo into a blind, over the web-workflow serial "
                    "websocket. Stops code.py, then walks you through the manual steps.",
        epilog="See docs/servo-onboarding.md before running it.")
    parser.add_argument("device", help="the blind's name (or host) in devices.json, "
                                       "such as upstairs-living-room-middle-blinds")
    parser.add_argument("role", choices=("lift", "tilt"),
                        help="the new servo's role: lift (ID 1, wheel mode) or tilt "
                             "(ID 2, angle limits 10-1000)")
    args = parser.parse_args()
    dev = device(args.device)
    try:
        ok = run(dev["name"], args.role, Repl(dev, echo=False))
    except (KeyboardInterrupt, EOFError):
        ok = _stopped(dev["name"], print, "stopped by the operator")
    except Exception as e:
        # Such as the websocket refusing: the web workflow doesn't start
        # after a watchdog or brownout reset.
        ok = _stopped(dev["name"], print, f"couldn't talk to the blind: {e!r}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
