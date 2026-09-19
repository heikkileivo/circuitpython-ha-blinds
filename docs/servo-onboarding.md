# Onboarding a servo

How to put a new SCS servo into a blind as its lift or its tilt, without editing code (#99).

A new servo arrives at its factory settings: ID 1, at 1 Mbps. The blind talks to its servos at 250,000 baud, with the lift at ID 1 and the tilt at ID 2. Until onboarding writes those settings into the servo, the blind can't talk to it, so nothing can stop it. That includes `boot.py`, `safemode.py` and the stall stop.

`tools/onboard.py` does the onboarding over the web-workflow serial websocket, and asks you to do each manual step. On the blind, it runs the `onboard` module. That module ships with the firmware but never runs at boot.

**Not supported yet:** STS3215 servos. The tool refuses one until #100 lands.

## Before

- **The host needs `websockets` 14 or later** (`pip install websockets`). It also needs the blind's entry in `devices.json`, the same one `deploy.py` uses.
- **Deploy first** if the blind's firmware predates the `onboard` module. Hash-check the deploy, as for any other (#106, #107).
- **Hard-reset the blind first if it last restarted from a watchdog or brownout.** Its **Reset cause** sensor in Home Assistant says which. The web workflow doesn't start after either of those resets, so the tool couldn't reach the blind.
- **Keep `/cp/serial/` closed otherwise.** Close any browser tab or other tool on the blind's serial console. Don't poll the blind's web workflow while the tool runs. The tool opens only the serial websocket.
- **Let the blind stop moving.** The tool stops `code.py` first, and whatever a servo was last told, it keeps doing.
- **Know which servo is which on the daisy chain.** You'll unplug the other servo, then plug it back in.

## The run

```
python3 tools/onboard.py <device-name> <lift|tilt>
```

For example, `python3 tools/onboard.py upstairs-living-room-middle-blinds lift` for a new lift in Middle.

The tool prints each step's result and waits at each of your steps:

1. **It stops `code.py`** with Ctrl-C. That also releases the watchdog, and the blind sits at the REPL from here on.
2. **You connect only the new servo.** Unplug the other servo and the old one, and plug in the new one, then press Enter.
3. **It onboards the servo.** It scans every ID at every baud rate, and goes on only if exactly one servo answered and nothing came back garbled. It prints what answered and the servo's registers 0–4, then writes the settings, reading each one back:
   1. `LOCK=0`
   2. the role's angle limits: lift 0/0 (wheel mode), tilt 10/1000
   3. the role's ID: lift 1, tilt 2
   4. baud rate 250,000
   5. everything read back at the new ID and rate
   6. `LOCK=1`
4. **You power-cycle the new servo.** Unplug it, plug it back in, then press Enter. The tool rescans and checks the settings survived.
5. **For a lift, it marks the stored travel unknown.** The spindle came out, so the travel is stale. The full travel and the cover state stay as they were.
6. **You plug the other servo back in,** then press Enter. The tool checks that exactly IDs 1 and 2 answer, at 250,000 baud, each with its role's angle limits.
7. **It soft-reboots the blind** and prints `PASS`.

**If a step fails,** the tool prints `FAIL` and why, then stops. A step fails on a refusal (two servos, a garbled reply, an STS3215), a setting that didn't read back or didn't survive the power cycle, a traceback on the blind, or no reply in time.
- The blind stays at the REPL. It doesn't reboot into settings nobody verified.
- **To recover, run the tool again.** Its scan finds the servo wherever the last run left it.
- To get the blind running without onboarding, once its servos are right: `python3 tools/repl.py <device-name> --reload`, or power-cycle it.

## After a lift swap

Check the blind in Home Assistant before trusting it again:

1. **A full close, then a full open.** The close re-anchors the travel at the down end sensor. The open confirms the full travel, or learns it again if the new servo's turns differ.
2. **A STOP partway through a move.** The blind should stop and brake where it is.
3. **The servo diagnostics:** servo health ok, and the lift's voltage, load and temperature in line with the tilt's and the other blinds'.
4. **No stall stop in the dead zone.** A new servo's pot and dead zone may differ from the old one's (#78). A full close and open that end only at the end sensors show that.

Open a new issue for any check that fails.

After a tilt swap, a close and an open that turn the slats through their range are enough.
