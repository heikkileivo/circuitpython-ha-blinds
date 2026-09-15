# CircuitPython HA Devices

Home-built CircuitPython devices that report to and are controlled from Home Assistant over MQTT: motorised window blinds and pulse-counting electricity and water meters.

## Language

### Blinds

**Cover state**:
What Home Assistant is told about a blind: open, closed, opening, closing, stopped or unknown.
_Avoid_: Position (when meaning the state), blinds state

**Tilt**:
How far the slats are turned, 0–100, as published to Home Assistant. While the blind is closed, it's the slats' angle. Otherwise it's the tilt the next close will drive the slats to. The slats sit at 50 meanwhile, as every open and close turns them to 50 before the lift moves.
_Avoid_: Tilt position, slat angle, servo angle (the tilt servo's raw reading)

**Travel**:
How far the blind is from its bottom end, measured in revolutions of the lift spindle.
_Avoid_: Position estimate, revolution count, height

**Full travel**:
The travel between the two end sensors, learned from a complete run from one to the other without stopping.
_Avoid_: Max revolutions, total travel, window height

**Interrupted move**:
A move that was under way when the blind lost power or reset, so it never reached a confirmed stop. After one, the blind's cover state is unknown until an end sensor re-anchors its travel.
_Avoid_: Unfinished move, crashed move

**Servo angle**:
The lift or tilt servo's raw rotational reading within a single turn.
_Avoid_: Position (when meaning the servo reading), servo position

**Dead zone**:
The part of each turn, at the wrap, where the lift servo's pot gives no reading: the servo angle holds at about 1018–1022, then 0–1, with the speed at 0, while the servo still turns. Crossing it takes about 100 ms at duty 800, and longer in proportion at lower duties.
_Avoid_: Dead band (the servo's position-mode tolerance, registers 26 and 27)

**End sensor**:
One of the two reed switches (up and down) that a magnet in the blind closes at the end of its travel. Its active zone can come in two parts: Middle's up end sensor is active over a lower zone about 0.3 revolutions of the lift tall, then, after a gap of about 0.05, over an upper zone that reaches the head rail. So a blind at the head rail reads its up end sensor active.
_Avoid_: Limit switch, limit sensor, stop pin, reed, up pin, down pin

**Re-seat**:
Crawling the blind back at approach speed onto an end sensor it has overshot, so that it rests with the sensor active.
_Avoid_: Nudge, correction

**Braking**:
The lift servo's rest state while powered: not driving, but resisting being turned, so the blind stays where it stopped. The opposite is **limp**: the roll turns freely.
_Avoid_: Holding torque, hold, lock

**Head rail**:
The fixed top of the blind assembly. Driving into it stalls the lift servo.
_Avoid_: Top stop, end stop (when meaning the top)

**Stall**:
The lift servo driven but not turning: a duty is commanded, yet its speed reads about 0 and its servo angle stays frozen. In the dead zone the angle freezes while the servo still turns, so a frozen angle there counts as a stall only after a longer window. Its own overload protection never trips in wheel mode, so only the firmware stops a stall.
_Avoid_: Jam, stuck, blocked

**Servo health**:
Whether a blind's servos answer and report no error, as published to Home Assistant: ok, no reply or error, taken as the worse of the lift and tilt servos.
_Avoid_: Servo status, servo fault, servo diagnostics (the voltage, temperature and load figures behind it)

### Failure modes

**Silent**:
A device that publishes nothing: no uptime, no state. Home Assistant stops hearing from it.
_Avoid_: Offline, dead, zombie

**Reset cause**:
Why a device last restarted, as published to Home Assistant. A restart the firmware triggers itself carries its own cause (brownout, other safe mode, MQTT escalation or restart loop). The one restart after a watchdog reset, which brings the web workflow up, keeps watchdog as its cause. Otherwise the cause is the chip's reported reason, such as power-on.
_Avoid_: Reset reason (the chip's raw report, which shows every firmware-triggered restart as a software reset)

**Liveness echo**:
A device's own uptime, which it publishes every 10 s and hears back from the broker. It's how a blind tells that its MQTT link is healthy: a successful publish only proves the send buffer took it.
_Avoid_: Heartbeat, ping (MQTT's own keep-alive probe)

**MQTT escalation**:
The blind restarting itself because its liveness echo has been missing for the escalation window, 5 minutes by default. It waits for a move to end first.
_Avoid_: Health reset, MQTT reset

**Restart loop**:
code.py running `main()` again after it fails. After a few quick failed runs in a row, 3 by default, the blind restarts instead. A run that lasted longer than the escalation window resets the count.
_Avoid_: Retry loop, boot loop

**Unresponsive**:
A device that still publishes (for example, uptime) but doesn't act on commands.
_Avoid_: Zombie, hung, stuck
