# Servo bench test: model, stall and overload behaviour

Results for [Bench-test the blinds servos: model, stall and overload behaviour][i21], part of
[Blinds reliability: plan for the open issues][i12]. It settles the **(bench)** points left open
by [the servo protocol research][research] and feeds
[Position isn't persisted; open can hit the head rail][i8].

Run 2026-09-11, 21:15–21:50 EEST, on the **Left blind** (192.168.1.162) in place, from the REPL
over the web-workflow serial websocket ([`tools/bench/`](../../tools/bench/)). The
human held the roll and judged braking by hand; the agent drove the UART. `code.py` was
stopped for the session and restarted afterwards. `bench.py` was deleted from the device.

## TL;DR

- **Lift servo: Feetech SCS115** (label). Its firmware differs from the tilt servo's: the
  register defaults differ (see below), and it has a live register at 69/70.
- **The lift servo's overload protection never trips in wheel mode.** It stalled against the head
  rail at duty 800 for 12.6 s with the defaults (80 %, 8 s): ERROR and status stayed 0. With the
  threshold lowered to 50 % / 1 s it didn't trip in 4 s of free running or in 3 s of
  head-rail stall either. **Stall protection has to be done by the firmware.** Nothing in the servo will
  stop a stalled lift on its own.
- **There's nothing to clear.** Since no overload latches in wheel mode, duty 0 stops a stalled
  lift at once, and status stays 0. So a boot-time re-init that writes duty 0 is enough
  to stop a servo left driving by a controller reset. HA only has to power-cycle the outlet when
  the servo doesn't reply at all.
- **A stall is obvious in the feedback:** speed 0 and position frozen within about 100 ms of
  hitting the rail. **Register 69/70 is not a usable current reading.** It reads −1 to −8 whether the servo
  is idle, running free or fully stalled.
- **A head-rail stall at 80 % sags the supply from 8.5 V to 5.8–7.4 V** (oscillating) and heats the
  servo by 10 °C in 13 s. The controller survived this time.
- **Fast reads work:** 300 of 300 block reads were clean on each servo, with a median of 1.65 ms (lift) and 1.77 ms
  (tilt), a max of 3.1 ms, `timeout=0.01`, one `uart.read(6+n)`, and no echo on the bus.
- **Direction:** a negative duty (bit 10 set) drives the blind **up**, with position counting up and
  `PRESENT_SPEED` positive, as `open()` assumes.
- **Braking:** torque switch **0 is free** (the roll turns by hand). **2 brakes** (stiff), and
  **1 with duty 0 brakes** about as much. Stopped at the up sensor, torque 2 held the position
  exactly (683 ±1 over 5 s). Torque 0 dropped about 50 counts in the first second, then settled.
- **The up-sensor window is narrow, about 0.85 s at duty 800.** In one head-rail run the blind passed
  it, and the sensor was off at the rail; in another it was on. The sensor also
  chattered once (off, then on again within 190 ms).

## 1. Model

| | Lift (ID 1) | Tilt (ID 2) |
|---|---|---|
| Label | **SCS115** | not read |
| Registers 0–4 | 3, 25, 1, 9, 15 | 0, 4, 1, 5, 15 |

Both read 15 at address 4. The research guessed that register might encode the model, but here it
matches the SCS15 table default, so registers 0–4 don't identify the model. Supply: 8.5–8.6 V
idle, which rules out the SCS0009 (4.8–6 V).

## 2. Registers

Read with one READ of 0–39 and one of 40–48, `code.py` stopped, blind closed.

| Addr | Meaning | Lift | Tilt |
|---|---|---|---|
| 5 / 6 | ID / baud | 1 / 2 (250 k) | 2 / 2 |
| 7 / 8 | return delay / status level | 0 / 1 | 0 / 1 |
| 9–12 | min / max angle limit | 0 / 0 (wheel) | 10 / 1000 |
| 13 | max temperature | 80 °C | 80 °C |
| 14 / 15 | max / min voltage | 9.0 / 4.0 V | 9.0 / 3.5 V |
| 16–17 | max torque | 1000 | 1000 |
| 18 | (undocumented) | 41 | 0 |
| 19 | unloading conditions | **45** (bits 0, 2, 3, 5) | 36 (bits 2, 5) |
| 20 | LED alarm | 45 | 37 |
| 21–23 | P / D / I | 45 / 60 / 0 | 10 / 10 / 0 |
| 24–25 | min starting force | 45 | 30 |
| 37 | protection torque | 20 % | 20 % |
| 38 | protection time | **200 (8 s)** | 75 (3 s) |
| 39 | overload torque | 80 % | 80 % |
| 48 | `LOCK` | **0** | 1 |
| 55 | (undocumented) | 1 | 0 |
| 67–68 | (undocumented) | mirrors position | mirrors position |
| 69–70 | "current" | 0x8002–0x8008 | 0 |

- The tilt servo matches the SCS15 table. The lift servo has bit 3 (current) set in unloading
  conditions and a longer protection time.
- **The lift servo's `LOCK` is 0 after power-up**, and `code.py` never writes it. So any EEPROM
  write to the lift servo persists. The research expected 1.
- **An EEPROM write can reply late.** The first write (38) sent no reply within the 10 ms timeout
  but did land; later writes replied in 3 ms. Use a longer timeout for EEPROM writes.

## 3. Stall in wheel mode

Samples are block reads of 56–70 at 10 Hz. Columns: position, `PRESENT_SPEED`, `PRESENT_LOAD`,
voltage (0.1 V), temperature, status (65), 69/70.

- **Hand-held roll** (duty −500 for 3 s, then −800 for 10 s): too strong to hold by hand. At 500
  the roll slowed to a stall only after 2.5 s; at 800 it kept turning at about half its free
  speed. Voltage fell to 7.8 V and 7.4 V. No overload.
- **Head rail** (duty −800 from the up sensor, 14 s cap): it hit the rail at about 1.1 s.
  Position then read 0 and speed 0 for 12.6 s. Voltage ran 5.8–7.4 V (8.5 V idle), temperature
  went from 21 to 31 °C, and load read −800 (the commanded duty) throughout. **ERROR and status stayed 0.**
  69/70 read −1 to −7.
- **Lowered threshold** (38 = 25 → 1 s, 39 = 50 %, written with `LOCK=0` after recording 20 / 200 /
  80): free run down at 800 for 4 s and a head-rail stall at 800 for 3 s, **no overload**. Then
  38 = 200 and 39 = 80 were written back, and the full register dump read back identical to the first one.

Whether protection is based on duty or on load can't be told apart, because it doesn't act in wheel mode at all. Not
tested: duty 1000 (it would stall harder on a shared supply that already sags to 5.8 V), and
overload in position mode (the tilt servo; it's documented).

## 4. Clearing overload

There's nothing to clear in wheel mode, because no overload latches. After a stall, duty 0 with torque still on stopped
the servo at once with status 0. Torque off then left it free, also with status 0.

For the boot-time re-init this means:

- A servo left driving by a controller reset (the servo keeps `GOAL_TIME` in its own SRAM) is
  stopped by writing duty 0. The controller can do that alone, so it should happen **early in boot, before
  Wi-Fi**, because until then the servo keeps pushing.
- It can't count on the servo's own protection to limit a stall while the controller is
  down.
- An HA power cycle is only needed if the servo doesn't reply to a PING.

## 5. Fast reads

`busio.UART(..., timeout=0.01)`, `reset_input_buffer()`, `write(req)`, one `read(6 + n)`, then
header, ID, length and checksum checks.

| | Reads | OK | Min | Median | p99 | Max |
|---|---|---|---|---|---|---|
| Lift, 56–66 (11 B) | 300 | 300 | 1617 µs | 1647 µs | 1983 µs | 3082 µs |
| Tilt, 56–66 (11 B) | 300 | 300 | 1739 µs | 1770 µs | 1983 µs | 2014 µs |

A 56–70 block (15 B) takes about 1.8–2.0 ms. During the motion tests, no sample in the printed
logs failed; one log was only shown in part. The only failure the counter recorded was the late
EEPROM write reply above. A raw PING gets back only the 6-byte reply (`ffff010200fc`), so
there's no echo to discard.

**Position wraps once per turn.** Around the pot's dead zone, position reads 1000–1021, then
0, and sometimes a stray mid value (532, 275). `PRESENT_SPEED` gives one absurd sample there
(15036, 16086, 26186, 27836, 30986, −16200). A stall detector must ignore single-sample outliers,
and a revolution counter must accept the stray value. Free speed at duty 800 is 1000–1450
counts/s going up and about 1400 going down. After a stop from duty 300, the servo coasts about 40 counts.

## 6. Wheel-mode details

- **Direction:** negative duty (bit 10) = up. Going up, position counts up and speed is positive;
  going down, position counts down and speed is negative.
- **Torque switch**, judged by hand turning the roll:

  | Register 40 | Feel | Held at the up sensor, 5 s hands-off |
  |---|---|---|
  | 0 | free, turns by hand | dropped about 50 counts in 1 s, then settled; sensor stayed on |
  | 1 (duty 0) | brakes, a bit easier than 2 | not measured |
  | 2 | brakes, stiff | 683 ±1, sensor on |

  The blind gets heavier the higher it rises, so it's most prone to drop at the top. Lower down,
  friction holds it in every mode (position 33 didn't move in 10 s with torque 0, 2 or 1).

## Not tested

- The `LOCK=1` EEPROM behaviour, whether max torque (16) caps the PWM duty, and duty 1001–1023.
- The refresh rate of 56–66.
- The tilt servo's model label.

None of these blocks a decision on the map.

[i8]: https://github.com/heikkileivo/circuitpython-ha-blinds/issues/8
[i12]: https://github.com/heikkileivo/circuitpython-ha-blinds/issues/12
[i21]: https://github.com/heikkileivo/circuitpython-ha-blinds/issues/21
[research]: https://github.com/heikkileivo/circuitpython-ha-blinds/blob/research/scs-servo-protocol/docs/research/scs-servo-protocol.md
