# Feetech SCS servo protocol: stall detection, protection and fast reads

Research for [Research: servo protocol for stall detection, protection and fast reads][i17],
part of [Blinds reliability: plan for the open issues][i12]. It feeds
[Position isn't persisted; open can hit the head rail][i8] and [Smooth speed profile][i10].

Researched 2026-09-11 against Feetech's reference drivers (pinned commits), Feetech's SCS memory
table, its protocol manuals and the SCS0009 product specification. Feetech's own site
(feetechrc.com / feetech.cn) did not resolve from here, so the Feetech documents are cited from
the copies that distributors Seeed, Waveshare and Switch Science host. The SHA-256 prefixes in
[Sources](#sources) pin the exact files read. Anything marked **(bench)** is not in any source
and has to be measured on the actual servos.

## TL;DR

- **Model family: SCS ("SCSCL" table), high confidence. Exact model: unknown.** The addresses in
  `packet.py` match Feetech's `SCSCL.h` one for one, apart from a `VERSION_H` typo. So do the
  big-endian word order and the EEPROM lock at 48. The register map cannot tell SCS0009, SCS15, SCS20 and SCS40 apart. Read the
  label, or registers 0–4.
- **`PRESENT_LOAD` is not a load measurement.** It is the PWM duty the servo drives into the motor
  (0–1000 = 0–100 %, bit 10 = direction). In wheel mode that is just the duty you commanded, so it
  says nothing about a stall. **`PRESENT_CURRENT` (69/70) is not in Feetech's SCS memory table,
  and the SCS0009 spec doesn't list current feedback.** Assume the servo has no current sensing
  until a bench read proves otherwise.
- **Protection lives in EEPROM:** max torque 16–17, unloading conditions 19, LED alarm 20,
  protection torque 37, protection time 38 and overload torque 39, plus the temperature and
  voltage limits at 13–15. None of them is a protection current. `LOCK` (48, SRAM, which powers
  up as 1) decides whether EEPROM writes are saved: write 0 and they are kept; leave it at 1 and
  they are lost at power-off.
- **Overload protection:** above *overload torque* for *protection time*, the servo cuts its
  output to *protection torque* (SCS15 defaults: 80 %, 4 s, 20 %; the SCS0009 spec says 80 % for
  2 s). Bit 5 of the ERROR byte in every reply packet shows it, and so does the status register
  65. The SCS0009 spec says **a new position command clears it**, so the documented behaviour
  doesn't need a power cycle. Whether a PWM (wheel-mode) write or a torque toggle also clears it
  is undocumented **(bench)**.
- **Fast reads:** nothing in the protocol needs the 18 ms sleep per byte. The reply follows the
  request after the *return delay* (register 7, 2 µs steps, default 0, at most 508 µs). It is
  `6 + n` bytes, about 40 µs per byte at 250 kbaud. Feetech's own driver just polls with a 10 ms
  inter-byte timeout. One `uart.read(6 + n)` with `timeout≈0.005` does the job, and a whole
  register block (56–66) reads in one transaction of about 1 ms.
- **Wheel mode:** confirmed. Min and max angle limit both 0 selects open-loop PWM mode.
  `GOAL_TIME` (44) is the duty: magnitude 0–1000, bit 10 is the direction. 1001–1023 can be
  encoded, but they are outside the documented scale.

## Which servo is this?

| Evidence | Points to |
|---|---|
| 28 of the 29 addresses in [`packet.py` `Address`][pk-addr] equal the `#define`s in Feetech's [`SCSCL.h`][scscl-h-map], including `CW_DEAD`/`CCW_DEAD` 26/27 and `PRESENT_CURRENT` 69/70. The 29th, `VERSION_H = 3`, is a typo for 4 | The file was copied from the **SCSCL** driver header |
| `LOCK` at 48. The SMS/STS table has the torque limit at 48–49 and `LOCK` at 55 ([`SMS_STS.h`][sts-h-lock]). Feetech's hex-command sheet also gives the lock address as "SCS 48, STS SMS 55" ([hex tables][hex]) | SCS, not STS/SMS |
| [`write_word`][pk-ww] sends `[h, l]` and [`read_2_bytes`][pk-r2] decodes high byte first. `SCSCL` sets `End = 1` ([`SCSCL.cpp`][scscl-end]), which is high byte first ([`SCS.cpp`][scs-endian]). The protocol manual: "potentiometer series… high byte before the low byte, … magnetic coding series… low byte before the high byte" ([manual 2019-12][proto2]) | SCS (potentiometer, TTL) series |
| Tilt positions are `tilt × 10` for 0–100, so 0–1000. SCS positions are 10-bit, 0–1023 ([SCS memory table][memtab]); STS positions are 0–4095 ([hex tables][hex]) | SCS |

**Confidence:** high that these are Feetech SCS servos (potentiometer, TTL, big-endian). **The
exact model can't be pinned down from the code.** SCS0009, SCS15, SCS20, SCS40 and the rest share
the SCSCL register layout. What differs per model is stall torque and stall current, the
protection defaults, the effective angle, and possibly whether 69/70 exists at all.

One weak hint for the lift servo: [Position isn't persisted; open can hit the head rail][i8] measures about 28 turns in 26 s, or about 65 RPM, at
`default_speed` 800 (80 % duty). The SCS15 table gives 53.7 RPM *no-load* at 7.4 V
([memtab][memtab]), which makes SCS15 unlikely unless the supply is well above 7.4 V. The SCS0009
does 100 RPM no-load at 6 V ([SCS0009 spec][scs0009] §5-2), which fits. This depends on the supply
voltage and on the spindle being direct-drive, so treat it as a hint only.

**To settle it (bench):** read the label. Or read addresses 0–4 (firmware and servo version; the
Python SDK's `ping()` reads 3–4 as the "model number" ([`protocol_packet_handler.py`][py-ping])).
In the SCS15 table the servo minor version (address 4) defaults to 15 ([memtab][memtab]), so it may
encode the model number. That is unconfirmed.

## 1. Load and current

**`PRESENT_LOAD` (60/61)**

- Unit 0.001: "the voltage duty cycle of the current control output driving motor"
  ([memtab][memtab], row 0x3C). The driver comment reads "输出至电机的电压百分比 (0~1000)", the
  output-to-motor voltage percentage ([`SCSCL.h`][scscl-h-load]).
- Sign: bit 10 is the direction and bits 0–9 the magnitude ([`SCSCL::ReadLoad`][scscl-readload]).
- **So it's the PWM duty, not a measured torque.** In position mode the duty climbs as the PID
  pushes against a load, so a stall does show up as a high duty. In wheel (PWM) mode the duty is
  whatever you wrote to `GOAL_TIME`, so load stays flat through a stall. One possible exception
  is overload protection: if it clamps the output to *protection torque*, load might drop to the
  protection level. That is unverified **(bench)**.

**`PRESENT_CURRENT` (69/70)**

- Defined in the 2024 `SCSCL.h` with bit 15 as the sign ([`SCSCL::ReadCurrent`][scscl-readcur]),
  but no unit is given anywhere for SCS.
- **Feetech's SCS memory table ends at 66 (`MOVING`) and has no current register** ([memtab][memtab]).
  Its status and unloading bit maps list bit 3 (current) as "None".
- The SCS0009 spec lists the feedback as "Load, Position, Speed, Input Voltage, Temperature", with
  no current ([SCS0009 spec][scs0009] §7-10).
- The Python SDK's `ERRBIT_OVERELE = 8` ([`protocol_packet_handler.py`][py-errbits]) sits in the
  protocol layer shared with SMS/STS. The same 69/70 current register appears in
  [`SMS_STS.h`][sts-h-cur], which suggests `SCSCL.h` inherited the definition from a common
  template.
- **Conclusion:** most likely absent on these servos. **(bench)** Read 69–70 during a move and a
  stall. If it stays 0 or constant, it's not implemented.

**`PRESENT_SPEED` (58/59):** step/s ([memtab][memtab]), with bit 15 as the sign
([`SCSCL::ReadSpeed`][scscl-readspeed]). The servo derives it from the potentiometer. **This is the
usable stall signal in wheel mode**, together with position (56/57). The pot has a dead zone (330°
sensor on the SCS0009, [spec][scs0009] §6-6), so expect a glitch once per turn **(bench)**.

**Update rate:** no source documents how often the servo refreshes 56–70. The only related figure
is "Max Position Update Rate 1 ms" in the SCS0009 spec ([spec][scs0009] §11). **(bench)** Sample at
50–100 Hz and look at how often the value changes.

## 2. Protection registers

All of these are in the **EPROM** area. Values are from Feetech's SCS15 memory table (firmware
`SCServo1.1-STM8-TTL(181129)`, [memtab][memtab] / [memtab-ws][memtab-ws]). **Defaults differ per
model** (compare the SCS0009 spec below), so read them from your servos.

| Addr | Name | SCS15 default | Unit / meaning |
|---|---|---|---|
| 7 | Return delay | 0 | 2 µs; max 254 → 508 µs |
| 8 | Response status level | 1 | 0 = only READ and PING reply; 1 = every instruction replies |
| 13 | Max temperature | 80 | °C |
| 14 / 15 | Max / min input voltage | 90 / 45 | 0.1 V (9.0 V / 4.5 V) |
| 16–17 | **Maximum torque** | 1000 | 0.001 of stall torque (1000 = 100 %) |
| 19 | **Unloading conditions** | 36 | bit0 voltage, bit2 temperature, bit5 overload (bits 1, 3, 4 "None"); 1 = protection on. 36 = overload + temperature |
| 20 | LED alarm conditions | 37 | same bits; 37 = voltage + temperature + overload |
| 24–25 | Minimum starting force | 30 | 0.001 of stall torque |
| 37 | **Protection torque** | 20 | % of max torque, the output *after* overload protection trips |
| 38 | **Protection time** | 100 | 40 ms (100 = 4 s; max ≈ 10 s), how long load must stay above overload torque |
| 39 | **Overload torque** | 80 | % of max torque, the threshold that starts the protection timer |

- There's **no protection-current register, and no SRAM torque limit**, on SCS. On STS, address
  48 is a torque limit ([`SMS_STS.h`][sts-h-lock]); on SCS it is `LOCK`.
- **Persistence:** `LOCK` (48) is SRAM, powers up as **1**, and means:
  - "Writing 0 disables the write lock, allowing the values written to EPROM addresses to be saved
    even when power is lost."
  - "Writing 1 enables the write lock, preventing the values written to EPROM addresses from being
    saved when power is lost" ([memtab-ws][memtab-ws], row 0x30).
  - So protection values written with `LOCK=0` survive a power cycle. Written with `LOCK=1`, they
    apparently still apply but only until power-off. The table implies this but doesn't say it
    outright **(bench)**. Feetech's own `unLockEprom`/`LockEprom` write 0 and then 1
    ([`SCSCL.cpp`][scscl-lock]), as `Reader.set_id` does.
- **EEPROM wear:** write persistent settings once, not on every boot. Or rewrite them with
  `LOCK=1` at each boot, which costs no flash wear but depends on the (bench) point above.
- **Whether *maximum torque* also caps the duty in PWM mode is not documented (bench).** Either
  way, the firmware already controls the duty directly, because it never has to command more
  than X.

**SCS0009 spec, for comparison** ([spec][scs0009] §7-11): "Over Load: stall at more than 80 % of
stall torque for 2 s enters protection; **sending a new position command clears the overload
protection flag**; the percentage and duration can be customised. Over Voltage: above 9 V or below
4 V enters protection and releases automatically when the voltage returns to normal. Over Hot:
above 70 °C, torque output is turned off." SCS0009 stall current is 1.0 A at 6 V (§5-5).

## 3. After a stall

- **What the servo does on its own:** once the output stays above *overload torque* for
  *protection time*, it cuts the output to *protection torque* (37). That is a reduced output,
  not necessarily zero ([memtab][memtab]). Bit 5 of the unloading conditions (19) has to be set
  for this; it is in the SCS15 default of 36.
- **Does it persist until power is cycled?**
  - Documented: no, not for overload in position mode. A new position command clears the flag
    ([SCS0009 spec][scs0009] §7-11).
  - Voltage protection clears by itself once the voltage recovers (same source).
  - Overheat release conditions aren't stated.
  - **Undocumented (bench):**
    - Whether a wheel-mode `GOAL_TIME` write, or `TORQUE_ENABLE` 0→1, clears it.
    - Whether, in PWM mode, the overload check runs on the output duty. `PRESENT_LOAD` *is* the
      duty, which suggests it does. If so, a hard free run (for example `default_speed` 800 = 80 %
      against an 80 % threshold) can't be told apart from a stall.
- **What survives a controller-only reset:** everything in the servo's SRAM, because the servo has
  its own MCU. On a servo power-up SRAM returns to its initial values: torque switch 0,
  `GOAL_TIME` 0, `LOCK` 1 ([memtab][memtab]). A controller reset changes none of that. This is the
  mechanism behind the Sep 5 observation in [Position isn't persisted; open can hit the head rail][i8].
- **How it shows over UART:**
  - Every reply is `FF FF ID LEN ERROR params… CHK` ([manual 2019-02][proto1] §1.2;
    [manual 2019-12][proto2] §1.2). ERROR carries the same bits as unloading conditions: bit0
    voltage, bit2 temperature, bit5 overload ([memtab-ws][memtab-ws] row 0x41).
  - The Python SDK decodes 1 voltage, 2 angle sensor, 4 overheat, 8 over-current and 32 overload
    ([`protocol_packet_handler.py`][py-errbits]).
  - The same bits can be read on demand from **servo status, address 65** (SRAM, read-only).
  - Feetech's C++ driver keeps the byte as `u8Status` ([`SCS::Read`][scs-read],
    [`SCS::Ack`][scs-ack]).
  - **This repo drops it:** [`Packet.payload_of`][pk-payload] returns `packet[4:-1]`, which
    *starts with* the ERROR byte. The read helpers only use `[-1]`/`[-2]`, so the status is never
    looked at. Surfacing it is one line: `packet[4]`.
- **Clearing it without a power cycle:**
  - Documented: send a position command, for the tilt servo in position mode.
  - The lift servo in wheel mode needs a bench test. Try in order: `GOAL_TIME`=0 → re-write a PWM
    value → torque 0→1 → temporarily switch to position mode (angle limits ≠ 0) and write
    `GOAL_POSITION` = present position → switch back.
  - **Don't send instruction 0x06:** the manual defines it as "RESET: reset the control table to
    the factory value" ([manual 2019-02][proto1] §1.3). That resets ID to 1 and baud to 1 Mbps,
    which would put both servos on ID 1 at the wrong baud rate.
  - The 2024 driver calls 0x06 `INST_RECOVERY` and adds an undocumented `INST_RESET` 0x0A
    ([`INST.h`][inst-h], [`SCS::Reset`][scs-reset]). Whether SCS firmware implements 0x0A is
    unknown. Only try it on a spare servo **(bench)**.

## 4. Fast reads

- **Packet sizes:**
  - READ request `FF FF ID 04 02 addr n CHK`, 8 bytes.
  - READ reply `FF FF ID (n+2) ERR d1…dn CHK`, **6 + n bytes** ([manual 2019-12][proto2] §1.3.2).
  - WRITE of *k* bytes: 7 + k bytes out, and a 6-byte status reply when status level is 1.
- **Wire time at 250 kbaud (8N1, 10 bits per byte):** 40 µs per byte.
  - Reading one word takes 16 bytes, about 0.64 ms.
  - Reading the whole 56–66 block (position, speed, load, voltage, temperature, async flag,
    status, moving) takes 8 + 17 = 25 bytes, about 1.0 ms.
  - Add the return delay (register 7, **default 0**, at most 508 µs, [memtab][memtab]) and the
    servo's own turnaround, which isn't documented **(bench)**.
- **No protocol reason for the 18 ms sleep per byte** in [`Reader.read_byte`][pk-readbyte]:
  - The protocol is a plain request/response exchange with back-to-back bytes.
  - Feetech's Arduino driver never sleeps. `readSCS` busy-polls the port with a **10 ms
    inter-byte timeout** (`IOTimeOut = 10`) ([`SCSerial.cpp`][scserial-read]) and finds the
    `FF FF` header by scanning at most 10 bytes ([`SCS::checkHead`][scs-checkhead]).
  - Feetech's Python SDK sets the packet timeout to `(len + 3) × byte-time + 50 ms`. The 50 ms is
    a PC USB-serial latency allowance ([`port_handler.py`][py-timeout]).
  - The per-byte sleep plus the "send a PING to pull more data" fallback are what make each
    register read take about 100–200 ms, as described in [Position isn't persisted; open can hit the head rail][i8]. The 10 ms sleep in
    [`write_mem`][pk-writemem] and the read-back in the `speed` setter add more.
- **One `busio.UART.read(n)` works:**
  - CircuitPython's `timeout` is "the timeout in seconds to wait for the first character and
    between subsequent characters when reading". It defaults to **1 s**
    ([`shared-bindings/busio/UART.c`][cp-uart-doc]). This repo doesn't set it
    ([`code.py`][cp-code]), so a lost reply costs a full second today.
  - On the ESP32-S3 port, `read(n)` busy-waits until `n` bytes have arrived or the inter-byte
    timeout (millisecond resolution) runs out, and returns `None` if nothing came
    ([`ports/espressif/.../UART.c`][cp-uart-esp]).
  - The busy-wait runs CircuitPython background tasks, but no other asyncio task. Keep the
    timeout small: 5–10 ms is plenty for a reply of 1 ms or less.
  - `receiver_buffer_size=32` is fine: the ESP32 port raises anything at or below the hardware
    FIFO size to FIFO + 8 ([same file][cp-uart-esp-buf]).
- **Suggested read:** `reset_input_buffer()` → `write(req)` → `buf = uart.read(6 + n)`. Then check
  the header, ID, `LEN == n + 2` and the checksum, and return `(data, buf[4])`.
- **Batch reads:** use one READ over a block instead of several small ones, the way Feetech's
  `FeedBack()` reads 56–70 in one go ([`SCSCL.cpp`][scscl-feedback]) and `ReadPosSpeed` reads 4
  bytes at 56 ([`scscl.py`][py-posspeed]).
- **SYNC READ (0x82):** it "is open to some serial bus servos" ([manual 2019-12][proto2] §1.3.7),
  and Feetech's Python SDK ships sync-read examples for SMS/STS and HLS but not for SCSCL
  ([repo tree][py-tree]). Don't count on it.
- **Echo caveat:** Feetech's reference interface keeps the controller's RX idle while it transmits
  ([SCS0009 spec][scs0009] §11 schematic, 74LVC1G125/126 plus TXEN). The current code works, which
  suggests this bus doesn't echo either. If a different interface does echo, drop `len(req)` bytes
  before parsing.

## 5. Wheel mode

- **Entering it:** write 0 to both *min angle limit* (9–10) and *max angle limit* (11–12)
  ([`SCSCL::PWMMode`][scscl-pwm]; memtab: "This value is set to 0 in motor mode"). They are EEPROM,
  so write them with `LOCK=0` to keep them, which [`Reader.set_as_motor`][pk-motor] does. The
  SCS0009 spec calls this "Mode 2: speed open-loop motor mode; speed keeps dropping as load
  increases" (§7-12). **It's duty, not closed-loop speed.**
- **Command:** `GOAL_TIME` (44–45) carries the duty magnitude, and **bit 10 (0x400) is the
  direction** ([`SCSCL::WritePWM`][scscl-pwm]; Python `WritePWM` → `scs_toscs(time, 10)`,
  [`scscl.py`][py-pwm]).
  - Scale: 1000 = 100 %. The Python example comments `WritePWM(1, 500)` as "maximum torque of
    50 %" ([`scscl/wheel.py`][py-wheel]), and the load register uses the same 0–1000 scale.
  - Magnitudes 1001–1023 can be encoded, but they're outside the documented range. Presumably they
    clamp **(bench)**.
  - Which physical rotation bit 10 gives is not documented **(bench)**. This repo's `open()` uses
    negative values.
- **Reading back:** `GOAL_TIME` returns the *commanded* duty, which is what `Servo.speed` reads
  today ([`blinds.py`][bl-speed]). The actual motion is `PRESENT_SPEED` (58) and
  `PRESENT_POSITION` (56).

## What this means

**Stall detection (for [Position isn't persisted; open can hit the head rail][i8])**

- Don't build it on `PRESENT_LOAD`; in wheel mode it only echoes the command. Don't count on
  `PRESENT_CURRENT` either until a bench read shows it's live.
- Detect a stall as *commanded |duty| > 0 while |`PRESENT_SPEED`| stays below a small threshold,
  or position doesn't move, for about 100–200 ms*. Base it on block reads of 56–66 at 20–50 Hz,
  which fast reads make possible.
- Check ERROR bit 5 (overload) on every reply as a second signal.
- Keep the servo's own overload protection as a backstop in case the controller is hung or
  resetting. If the servo checks overload against the output duty, it can only trip when the
  commanded duty is above *overload torque*, so a stall at a lower duty would never trip it. First
  test whether it trips at all in PWM mode against a real stall **(bench)**. If it does, set
  protection time to about 0.5–1 s (12–25 × 40 ms) and protection torque to 0–10 %, write them
  once with `LOCK=0`, and check that normal full-speed travel doesn't trip them.
- Cap the duty in firmware, because stall current scales with it. Also consider *maximum torque*
  (16), if the bench shows it limits PWM too.

**Boot-time servo re-init (for [Position isn't persisted; open can hit the head rail][i8])**

1. First, a **broadcast** (ID 0xFE) WRITE `GOAL_TIME = 0`, then a broadcast WRITE
   `TORQUE_ENABLE = 0`. All servos act on it, and none replies ([manual 2019-02][proto1] §1.1).
   It works even before we know which servo is in which state.
2. Per ID: PING. A reply means the servo is present, and its ERROR byte is the current status.
3. Read 62–66 (voltage, temperature, async flag, status, moving) in one READ.
4. Write speed 0 and torque off per ID, and confirm them from the replies and a read-back.
5. Read 0–39 once and log it as a config snapshot (baud, return delay, the protection registers,
   the angle limits).
6. If the overload bit is set: for the tilt servo, write `GOAL_POSITION` = present position to
   clear it (documented). For the lift servo, apply whatever clearing step the bench test finds.
   Publish a fault if it won't clear, so HA can cycle the outlet.
7. Never use instruction 0x06 (factory reset).

The servo torque switch takes 0 (off / "damping"), 1 (on) or 2 ("free") per the SCS15 table.
Whether 0 actually brakes compared with 2 is worth checking for the Left blind that settles off
its sensor **(bench)**.

**Faster reads (for [Position isn't persisted; open can hit the head rail][i8] and [Smooth speed profile][i10])**

- Replace `read_byte`/`read_packet` with one `read(6 + n)` and `uart.timeout ≈ 0.005`.
- Parse and return the ERROR byte.
- Drop the PING fallback and the sleeps.
- Read blocks instead of single registers.
- During a speed profile, write speed without the read-back check. The write's own status reply
  (ERROR byte) already confirms it arrived.
- Expect about 1–2 ms per transaction, which easily allows 20–50 Hz for both servos.

## Must be confirmed on the servos (bench checklist)

1. Model: the label, or registers 0–4.
2. Registers 7, 8, 13–39 on both servos: the actual defaults.
3. Whether 69–70 changes during a move or stall (is there current sensing?).
4. `PRESENT_LOAD` in wheel mode: during free running, during a stall, and after overload
   protection trips.
5. Whether overload protection trips in PWM mode on a real stall, and whether a hard free run at
   80 % can trip it.
6. What clears overload in wheel mode (PWM write, torque toggle, mode switch plus position write).
7. Whether EEPROM writes with `LOCK=1` take effect until power-off.
8. Whether *maximum torque* (16) limits the PWM duty.
9. Direction of bit 10, and what 1001–1023 does.
10. Servo turnaround time, and the refresh rate of 56–66.
11. Torque switch 0 vs 2: holding and damping behaviour.

## Side findings in `packet.py`

- [`VERSION_H = 3`][pk-addr] should be 4 ([`SCSCL.h`][scscl-h-map]).
- The ERROR byte is ignored (see §3), and so is the checksum in `read_packet` (it's computed and
  then not compared; `Packet.is_valid` does check it afterwards).
- `busio.UART` runs with the default 1 s timeout ([`code.py`][cp-code]).
- `Servo.speed` returns the commanded `GOAL_TIME`, not the measured speed. `get_time` returning
  `None` would raise inside the `&` ([`blinds.py`][bl-speed]).

## Sources

Feetech reference code (pinned commits):

- FTServo_Arduino @ `64922cd`: [`SCSCL.h`][scscl-h-map], [`SCSCL.cpp`][scscl-end], [`SCS.cpp`][scs-read], [`INST.h`][inst-h], [`SCSerial.cpp`][scserial-read], [`SMS_STS.h`][sts-h-lock]
- FTServo_Python @ `a203373`: [`scscl.py`][py-pwm], [`protocol_packet_handler.py`][py-errbits], [`port_handler.py`][py-timeout], [`scscl/wheel.py`][py-wheel], [tree][py-tree]

Feetech documents (distributor-hosted copies; SHA-256 prefix of the file read):

- [memtab] SCS15 memory table, firmware `SCServo1.1-STM8-TTL(181129)`, *Analysis of SCS Memory Table* (Seeed copy), `5d52179f85435851`
- [memtab-ws] The same table, Waveshare copy (cleaner translation), `8dc3232f1c714e4c`
- [proto1] *Serial Bus Smart Control servo Communication Protocol Manual* V1.01, 2019-02-19 (Seeed copy), `50e981ddd87042d3`
- [proto2] *Communication Protocol User Manual* (EN, 2019-12-18), Waveshare copy, `3bf67ac6e28ac37e`
- [scs0009] SCS0009 product specification A/0, 2020-11-23 (Switch Science copy), `56b0e9ff6956fd67`
- [hex] *Hexadecimal instructions generate tables* (Seeed copy)

CircuitPython 10.3.0: [`shared-bindings/busio/UART.c`][cp-uart-doc], [`ports/espressif/common-hal/busio/UART.c`][cp-uart-esp]

This repo @ `cd8c9cc`: [`packet.py`][pk-addr], [`blinds.py`][bl-speed], [`code.py`][cp-code]

[i8]: https://github.com/heikkileivo/circuitpython-ha-blinds/issues/8
[i10]: https://github.com/heikkileivo/circuitpython-ha-blinds/issues/10
[i12]: https://github.com/heikkileivo/circuitpython-ha-blinds/issues/12
[i17]: https://github.com/heikkileivo/circuitpython-ha-blinds/issues/17
[scscl-h-map]: https://github.com/ftservo/FTServo_Arduino/blob/64922cda46e56b21b8c1d9e830d936a1941645ae/src/SCSCL.h#L11-L47
[scscl-h-load]: https://github.com/ftservo/FTServo_Arduino/blob/64922cda46e56b21b8c1d9e830d936a1941645ae/src/SCSCL.h#L68
[scscl-end]: https://github.com/ftservo/FTServo_Arduino/blob/64922cda46e56b21b8c1d9e830d936a1941645ae/src/SCSCL.cpp#L10-L13
[scscl-lock]: https://github.com/ftservo/FTServo_Arduino/blob/64922cda46e56b21b8c1d9e830d936a1941645ae/src/SCSCL.cpp#L70-L78
[scscl-feedback]: https://github.com/ftservo/FTServo_Arduino/blob/64922cda46e56b21b8c1d9e830d936a1941645ae/src/SCSCL.cpp#L80-L87
[scscl-readspeed]: https://github.com/ftservo/FTServo_Arduino/blob/64922cda46e56b21b8c1d9e830d936a1941645ae/src/SCSCL.cpp#L102-L116
[scscl-readload]: https://github.com/ftservo/FTServo_Arduino/blob/64922cda46e56b21b8c1d9e830d936a1941645ae/src/SCSCL.cpp#L118-L132
[scscl-readcur]: https://github.com/ftservo/FTServo_Arduino/blob/64922cda46e56b21b8c1d9e830d936a1941645ae/src/SCSCL.cpp#L167-L181
[scscl-pwm]: https://github.com/ftservo/FTServo_Arduino/blob/64922cda46e56b21b8c1d9e830d936a1941645ae/src/SCSCL.cpp#L183-L203
[scs-endian]: https://github.com/ftservo/FTServo_Arduino/blob/64922cda46e56b21b8c1d9e830d936a1941645ae/src/SCS.cpp#L31-L59
[scs-read]: https://github.com/ftservo/FTServo_Arduino/blob/64922cda46e56b21b8c1d9e830d936a1941645ae/src/SCS.cpp#L170-L217
[scs-checkhead]: https://github.com/ftservo/FTServo_Arduino/blob/64922cda46e56b21b8c1d9e830d936a1941645ae/src/SCS.cpp#L278-L298
[scs-ack]: https://github.com/ftservo/FTServo_Arduino/blob/64922cda46e56b21b8c1d9e830d936a1941645ae/src/SCS.cpp#L300-L330
[scs-reset]: https://github.com/ftservo/FTServo_Arduino/blob/64922cda46e56b21b8c1d9e830d936a1941645ae/src/SCS.cpp#L436-L442
[inst-h]: https://github.com/ftservo/FTServo_Arduino/blob/64922cda46e56b21b8c1d9e830d936a1941645ae/src/INST.h#L26-L35
[scserial-read]: https://github.com/ftservo/FTServo_Arduino/blob/64922cda46e56b21b8c1d9e830d936a1941645ae/src/SCSerial.cpp#L10-L77
[sts-h-lock]: https://github.com/ftservo/FTServo_Arduino/blob/64922cda46e56b21b8c1d9e830d936a1941645ae/src/SMS_STS.h#L38-L40
[sts-h-cur]: https://github.com/ftservo/FTServo_Arduino/blob/64922cda46e56b21b8c1d9e830d936a1941645ae/src/SMS_STS.h#L52-L53
[py-pwm]: https://github.com/ftservo/FTServo_Python/blob/a203373036723e0d98c6c49b67cf09a9ee299220/scservo_sdk/scscl.py#L55-L104
[py-posspeed]: https://github.com/ftservo/FTServo_Python/blob/a203373036723e0d98c6c49b67cf09a9ee299220/scservo_sdk/scscl.py#L72-L76
[py-errbits]: https://github.com/ftservo/FTServo_Python/blob/a203373036723e0d98c6c49b67cf09a9ee299220/scservo_sdk/protocol_packet_handler.py#L17-L22
[py-ping]: https://github.com/ftservo/FTServo_Python/blob/a203373036723e0d98c6c49b67cf09a9ee299220/scservo_sdk/protocol_packet_handler.py#L255-L275
[py-timeout]: https://github.com/ftservo/FTServo_Python/blob/a203373036723e0d98c6c49b67cf09a9ee299220/scservo_sdk/port_handler.py#L65-L67
[py-wheel]: https://github.com/ftservo/FTServo_Python/blob/a203373036723e0d98c6c49b67cf09a9ee299220/scscl/wheel.py#L41-L72
[py-tree]: https://github.com/ftservo/FTServo_Python/tree/a203373036723e0d98c6c49b67cf09a9ee299220
[memtab]: https://files.seeedstudio.com/wiki/robotics/Actuator/feetech/Analysis_of_SCS_Memory_Table.xlsx
[memtab-ws]: https://files.waveshare.com/upload/5/5c/SCS_Series_Memory_Table_Analysis.xls
[proto1]: https://files.seeedstudio.com/wiki/robotics/Actuator/feetech/Communication_Protocol_Manual.pdf
[proto2]: https://files.waveshare.com/upload/2/27/Communication_Protocol_User_Manual-EN%28191218-0923%29.pdf
[scs0009]: https://pages.switch-science.com/comparison/files/feetech/serial-scs/SCS0009_datasheet.pdf
[hex]: https://files.seeedstudio.com/wiki/robotics/Actuator/feetech/Hexadecimal_instructions_generate_tables.xlsx
[cp-uart-doc]: https://github.com/adafruit/circuitpython/blob/10.3.0/shared-bindings/busio/UART.c#L56-L78
[cp-uart-esp]: https://github.com/adafruit/circuitpython/blob/10.3.0/ports/espressif/common-hal/busio/UART.c#L308-L359
[cp-uart-esp-buf]: https://github.com/adafruit/circuitpython/blob/10.3.0/ports/espressif/common-hal/busio/UART.c#L147-L149
[cp-code]: https://github.com/heikkileivo/circuitpython-ha-blinds/blob/cd8c9cc/devices/blinds/code.py#L204-L207
[pk-addr]: https://github.com/heikkileivo/circuitpython-ha-blinds/blob/cd8c9cc/devices/blinds/packet.py#L11-L43
[pk-payload]: https://github.com/heikkileivo/circuitpython-ha-blinds/blob/cd8c9cc/devices/blinds/packet.py#L81-L82
[pk-readbyte]: https://github.com/heikkileivo/circuitpython-ha-blinds/blob/cd8c9cc/devices/blinds/packet.py#L152-L167
[pk-writemem]: https://github.com/heikkileivo/circuitpython-ha-blinds/blob/cd8c9cc/devices/blinds/packet.py#L169-L179
[pk-ww]: https://github.com/heikkileivo/circuitpython-ha-blinds/blob/cd8c9cc/devices/blinds/packet.py#L197-L201
[pk-r2]: https://github.com/heikkileivo/circuitpython-ha-blinds/blob/cd8c9cc/devices/blinds/packet.py#L210-L219
[pk-motor]: https://github.com/heikkileivo/circuitpython-ha-blinds/blob/cd8c9cc/devices/blinds/packet.py#L127-L135
[bl-speed]: https://github.com/heikkileivo/circuitpython-ha-blinds/blob/cd8c9cc/devices/blinds/blinds.py#L71-L96
