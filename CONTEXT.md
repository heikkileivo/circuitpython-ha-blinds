# CircuitPython HA Devices

Home-built CircuitPython devices that report to and are controlled from Home Assistant over MQTT: motorised window blinds and pulse-counting electricity and water meters.

## Language

### Blinds

**Cover state**:
What Home Assistant is told about a blind: open, closed, opening, closing, stopped or unknown.
_Avoid_: Position (when meaning the state), blinds state

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

**End sensor**:
One of the two reed switches (up and down) that a magnet in the blind closes at the end of its travel. Its active zone is only a few millimetres long.
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
The lift servo driven but not turning: a duty is commanded, yet its speed reads about 0 and its servo angle stays frozen. Its own overload protection never trips in wheel mode, so only the firmware stops a stall.
_Avoid_: Jam, stuck, blocked

**Servo health**:
Whether a blind's servos answer and report no error, as published to Home Assistant: ok, no reply or error, taken as the worse of the lift and tilt servos.
_Avoid_: Servo status, servo fault, servo diagnostics (the voltage, temperature and load figures behind it)

### Failure modes

**Silent**:
A device that publishes nothing: no uptime, no state. Home Assistant stops hearing from it.
_Avoid_: Offline, dead, zombie

**Reset cause**:
Why a device last restarted, as published to Home Assistant. A restart the firmware triggers itself carries its own cause (brownout, MQTT escalation or restart loop). Otherwise the cause is the chip's reported reason, such as power-on or watchdog.
_Avoid_: Reset reason (the chip's raw report, which shows every firmware-triggered restart as a software reset)

**Unresponsive**:
A device that still publishes (for example, uptime) but doesn't act on commands.
_Avoid_: Zombie, hung, stuck
