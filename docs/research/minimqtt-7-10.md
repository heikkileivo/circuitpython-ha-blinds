# MiniMQTT 7.10.0 and ConnectionManager 3.1.1: loop timing, last will, stale sockets

Research for [Research: MiniMQTT 7.10 loop timing, last will and stale-socket release](https://github.com/heikkileivo/circuitpython-ha-blinds/issues/15),
part of [Blinds reliability: plan for the open issues](https://github.com/heikkileivo/circuitpython-ha-blinds/issues/12).
Researched 2026-09-11.

**Versions on the devices:** CircuitPython 9.1.1 / 9.1.3, `adafruit_minimqtt` 7.10.0, `adafruit_connection_manager` 3.1.1.

**Primary sources (all links are pinned to a tag):**

- MiniMQTT [7.10.0 `adafruit_minimqtt.py`][mm0], plus [7.10.4][mm4] and [8.1.0][mm8] for the later fixes, and the [release notes][mm-rel].
- ConnectionManager [3.1.1 `adafruit_connection_manager.py`][cm]. Between 3.1.1 and 3.1.8 the only commit that touches this file is `90a2512` "change to ruff", and a whitespace-insensitive diff shows it only reformats and removes pylint comments. So everything below also holds for 3.1.8.
- CircuitPython 9.1.3 espressif [`common-hal/socketpool/Socket.c`][cps], [`shared-bindings/socketpool/Socket.c`][cpb] and [`esp-idf-config/sdkconfig.defaults`][sdk]. All three files are byte-identical at 9.1.1, and the TinyS3 board sdkconfig only sets the hostname ([board sdkconfig][tinys3]).
- The [MQTT 3.1.1 specification][spec], and Mosquitto [v2.0.22 `handle_connect.c`][mosq] for the broker side.

"L" numbers are line numbers in the pinned file.

---

## Short answers

| # | Question | Answer (7.10.0 / 3.1.1 on CP 9.1.x) |
|---|---|---|
| 1 | `loop()` timing | `loop(timeout)` raises if `timeout < socket_timeout`. `timeout == socket_timeout` is allowed, and the default `loop()` (timeout 0) raises. Neither MiniMQTT nor CircuitPython sets a 1 s floor, so a sub-second value such as `socket_timeout=0.25` is legal. An idle `loop(t)` blocks about `t`: it never returns early, even after a message arrives. A half-received packet blocks for up to `socket_timeout` per stalled read, then raises `OSError(ETIMEDOUT)`. If bytes keep trickling in, it blocks for up to `recv_timeout` (10 s) and raises `MMQTTException`. A socket the broker closed cleanly (FIN) makes `loop()` spin for `recv_timeout` before it raises. |
| 2 | `ping()` inside `loop()` | `loop()` pings when at least `keep_alive` seconds have passed since the last packet the **client sent**. That covers CONNECT, PUBLISH, SUBSCRIBE, UNSUBSCRIBE and PINGREQ; received packets don't count. `ping()` blocks until PINGRESP arrives, for up to `keep_alive` (60 s) on a silent link, and ignores `loop()`'s own timeout. A successful `publish()` resets the timer. |
| 3 | Last will | `will_set(topic=None, payload=None, qos=0, retain=False)`. It must be called before `connect()`, and it raises if `_is_connected`, which stays True after a socket error. It is stored on the client and re-sent in every CONNECT, so it survives `reconnect()`. It does **not** survive a client rebuild: a new `MQTT()` starts with no will. Retain is supported. |
| 4 | Stale socket | Yes: `connection_manager_close_all(pool)` closes and **unregisters** every socket of that pool, so a new `connect()` on the **same** pool works. `close_socket(sock)` does the same for one socket. `free_socket()` is wrong: it hands the same dead socket back. A fresh `SocketPool` also works, but it leaks the old socket whenever 7.10.0's `disconnect()` fails to close it. |
| 5 | `on_disconnect` | In 7.10.0 it is called only from `disconnect()` (and from `deinit()` and `__exit__`, which call `disconnect()`), never on a socket error. In 8.x `reconnect()` also calls it. |
| 6 | Newer releases | 7.10.4 fixes the stale-socket lockout, 7.10.6 fixes truncated large publishes (the ~2.7 KB discovery limit), and 8.0.0 makes `reconnect()` disconnect first. No release makes `loop()` non-blocking, drops the inline `ping()`, or calls `on_disconnect` on errors. 8.1.0 is in the 2026-09-10 **9.x** mpy bundle, so it can be installed on CP 9.1.x. It hasn't been run on the devices yet. |

---

## 1. `loop()` timing

### Minimum values

- The constructor defaults are `keep_alive=60`, `recv_timeout=10`, `socket_timeout=1` and `connect_retries=5` ([7.10.0 L141-158][mm0-init]).
- The only constructor check is `recv_timeout` > `socket_timeout` ([L166-169][mm0-rt]). There is no lower bound on `socket_timeout`. The `int` type hint is not enforced.
- `loop()` raises `MMQTTException` if `timeout < self._socket_timeout` ([L924-929][mm0-loopchk]), so the smallest legal `loop()` timeout is `socket_timeout` itself. The default `loop(timeout=0)` therefore raises when `socket_timeout=1`. 7.10.5 changed the default to `1.0` and the error message to ">=" ([PR #228][pr228]; [8.1.0 L983-994][mm8-loop]).
- `socket_timeout` is applied once, when the socket is created: ConnectionManager calls `socket.settimeout(timeout)` before `connect()` ([CM L245-253][cm-gcs]), and MiniMQTT passes `timeout=self._socket_timeout` ([L468-476][mm0-getsock]). So one value sets both the TCP-connect timeout and the per-`recv_into` timeout for the life of the socket.
- CircuitPython's `settimeout()` takes a float and stores `1000 * value` in milliseconds ([CP shared-bindings L367-381][cpb-st]). Sub-second timeouts therefore work at the platform level.
- `0` means non-blocking, and it breaks connecting. The emulated connect timeout counts `timeout_left` down in 100 ms `select()` steps and raises `ETIMEDOUT` once it reaches 0 ([CP L42][cps-poll], [L402-471][cps-connect]). With a timeout of 0 the loop body never runs, so any connect that doesn't finish immediately fails. Mid-packet reads would also raise `EAGAIN`. **So the practical floor is a small positive value such as 0.25 s, not 1 s.**
- Values under 100 ms still wait at least one 100 ms `select()` during connect ([L444-449][cps-connect]).
- *Hardware check:* whether connect is reliable over Wi-Fi with a sub-second `socket_timeout`. This can't be settled from source.

### How long one idle `loop()` call blocks

- `loop()` keeps calling `_wait_for_msg()` until `elapsed > timeout` (strictly greater), and it does **not** return after the first message ([L937-958][mm0-loopbody]).
- Each idle `_wait_for_msg()` blocks in `recv_into` for one `socket_timeout`. CircuitPython's espressif sockets are all non-blocking ([CP L208-209][cps-nb]). `recv_into` polls `lwip_recv` until `supervisor_ticks_ms64() - start >= timeout_ms`, then raises `OSError(ETIMEDOUT)` ([CP L525-574][cps-recv]). MiniMQTT turns `ETIMEDOUT`/`EAGAIN` into "no message" ([L975-981][mm0-wfm1]).
- So an idle `loop(t)` blocks for `t` rounded up to the next whole multiple of `socket_timeout`. With `t == socket_timeout` that is about one `socket_timeout`. It can be two if the elapsed time reads exactly `t`, because the check is a strict `>` ([L956][mm0-loopbody]).
- When the keep-alive is due, add the `ping()` time (see section 2).
- Received messages are dispatched synchronously inside `loop()` ([L1023][mm0-onmsg]). The time a callback takes is added to the call, and an exception from a callback propagates out of `loop()`.

### When a message is half-received

- `_wait_for_msg()` guards only the **first** byte read with `try/except OSError` ([L975-982][mm0-wfm1]). The rest of the packet is read unguarded: the remaining length, topic, packet id and payload ([L1000-1020][mm0-wfm2]).
- `_sock_exact_recv()` loops on `recv_into` until it has every byte, and gives up after `recv_timeout` ([L1062-1080][mm0-ser]).
- Each `recv_into` inside that loop can itself raise `OSError(ETIMEDOUT)` after `socket_timeout` of silence ([CP L562-573][cps-recv]). That error isn't caught, so it propagates out of `loop()` as a bare `OSError`.
- Result:
  - A stall of more than `socket_timeout` mid-packet makes `loop()` raise `OSError(ETIMEDOUT)` after about `socket_timeout`.
  - Bytes that keep trickling in make it raise `MMQTTException("Unable to receive … within 10 seconds")` after `recv_timeout`.
  - Either way, part of the packet has already been consumed and the MQTT stream is **out of sync**. The only safe recovery is to tear the connection down.
- A small PUBLISH (such as a cover command) normally arrives in one TCP segment, so this is rare on a LAN. It gets more likely as `socket_timeout` gets smaller. *Hardware check.*

### A socket the broker closed cleanly (FIN)

- When Mosquitto stops cleanly it closes its sockets. On the device, `lwip_recv` then returns `0`. CircuitPython's loop exits on any return other than `-1`, so `recv_into` returns `0` immediately instead of waiting ([CP L534-540, L565][cps-recv]).
- `_sock_exact_recv(1)` then has `to_read = 1` and calls `recv_into` again and again, each call returning 0 at once. It **busy-spins for `recv_timeout` (10 s)** and then raises `MMQTTException` ([L1067-1080][mm0-ser]). The first-byte read has no timeout argument, so `read_timeout = self._recv_timeout` ([L1071][mm0-ser]).
- This is what the blinds hit when HA shut down cleanly on Sep 10: a 10 s freeze inside `loop()`, then the reconnect lockout described in section 4.
- *Hardware check:* that lwIP keeps returning 0, not `-1`/`ENOTCONN`, on repeated reads after FIN. If it returns `-1`, the call raises `OSError(ETIMEDOUT)` after `socket_timeout` instead. Either way `loop()` raises within `recv_timeout`.

### An RST or a silent peer

- CircuitPython's `recv_into` loops while `lwip_recv` returns `-1`, and it doesn't check `errno` ([CP L534-557][cps-recv]). A connection that was reset, or whose peer vanished, therefore looks exactly like an idle one to `loop()`: every call just times out.
- Only `send()` reports the error. It raises `OSError` for `ECONNRESET`/`ENOTCONN` and similar ([CP L576-603][cps-send]). So on such a link the first sign of trouble is a failed `publish()`, or a ping timeout.

---

## 2. `ping()` inside `loop()`

- **When:** at the top of every `loop()` iteration, if `ticks_diff(now, _last_msg_sent_timestamp) >= keep_alive` ([L938-946][mm0-loopbody]). The ping fires as soon as `keep_alive` is reached, not earlier.
- **What resets the timer:** only packets the client *sends*:

  | Packet | Where the timer is set |
  |---|---|
  | CONNECT | [L522][mm0-connts] |
  | PUBLISH | after the three `send()`s, [L671-674][mm0-pubts] |
  | SUBSCRIBE | [L752][mm0-subts] |
  | UNSUBSCRIBE | [L828][mm0-unsubts] |
  | PINGREQ | [L597][mm0-ping] |

  `disconnect()` sets it to 0 ([L582][mm0-disc]). Received messages don't reset it, and neither does the QoS 1 PUBACK the client sends back ([L1024-1027][mm0-wfm2]). **Yes, a successful `publish()` resets it.** A publish whose `send()` raises does not.
- **How long it blocks:** `ping()` sends PINGREQ and then loops on `_wait_for_msg()` until it sees PINGRESP. It raises `MMQTTException` only after `keep_alive` seconds ([L586-607][mm0-ping]).
  - Healthy link: one round trip.
  - Silent or reset link: about `keep_alive` plus one `socket_timeout`, which is **about 61 s** with the devices' settings. The blinds' 16 s watchdog would fire first.
  - FIN'd link: about `recv_timeout` (10 s), because of the spin described in section 1.
  - `loop()` doesn't enforce its own timeout while `ping()` runs; it checks only afterwards ([L947-951][mm0-loopbody]).
  - An upstream issue about `loop()` blocking on PINGREQ with `timeout=0` was closed years ago ([MiniMQTT #86][i86]), and the design is unchanged in 8.1.0 ([L1003-1013][mm8-loop], [ping L649-670][mm8-ping]).
- **Broker side:** the broker must receive *some* packet from the client within 1.5 × `keep_alive` (90 s), or it drops the connection "as if the network had failed" and publishes the will ([MQTT-3.1.2-24][spec-ka]).
  - If nothing is published for `keep_alive`, the device pings only if `loop()` runs at some point between 60 s and 90 s after the last send.
  - Today's blinds skip both `loop()` and publishing while moving (see [Blocking MQTT loop starves asyncio](https://github.com/heikkileivo/circuitpython-ha-blinds/issues/5)).
- **Consequence:** if the device publishes something at least every `keep_alive / 2`, `loop()` never takes the blocking ping branch. The same publishes also keep the broker satisfied.
  - The cost is that the device then learns about a dead broker only from a failed `send()`.
  - On a silent link, lwIP buffers sends until its retransmissions give up, which takes several initial RTOs (`CONFIG_LWIP_TCP_RTO_TIME=3000`, [sdkconfig L74][sdk-lwip]). Until then **`publish()` "succeeds" into the lwIP buffer.**
  - So the meters' 90 s `STALL_TIMEOUT`, which counts successful publishes, can't see a silent dead link until lwIP gives up. *Hardware check:* how long that takes.

---

## 3. Last will

- **Signature (7.10.0):** `will_set(self, topic=None, payload=None, qos=0, retain=False)` ([L270-303][mm0-will]).
  - `payload` may be `int`, `float` or `str`. `bytes` raises `MMQTTException("Invalid message data type.")`, and `None` becomes `""`.
- **Before `connect()`:** yes. It raises `MMQTTException("Last Will should only be called before connect().")` if `self._is_connected` ([L292-293][mm0-will]).
  - After a socket error `_is_connected` stays True (section 4), so `will_set()` on the *same* client raises until something calls `disconnect()`.
- **Survives `reconnect()`:** yes. The will lives in `_lw_topic`, `_lw_msg`, `_lw_qos` and `_lw_retain` ([L223-227][mm0-lwinit]). Every `_connect()` writes them into CONNECT ([L500-505][mm0-willenc], [L515-518][mm0-willsend]), and `reconnect()` just calls `connect()` ([L893-915][mm0-reconn]).
- **Survives a client rebuild:** no. A new `MQTT()` starts with `_lw_topic = None` ([L223-227][mm0-lwinit]). Call `will_set()` on every new client before its first `connect()`.
- **Retain:** supported. Bit 5 of the connect flags is set from `retain` ([L505][mm0-willenc]), which is the Will Retain bit ([spec 3.1.2.7][spec-wr]). QoS goes in bits 3-4 ([L504][mm0-willenc]).
- **Broker semantics** ([spec 3.1.2.5][spec-wf], [3.14.4][spec-disc]):
  - The will is published when the connection closes *without* a DISCONNECT, including after a keep-alive timeout.
  - A clean `disconnect()` makes the broker discard the will. So if you want HA to show "offline" after a deliberate disconnect, publish it yourself.
- **Forward-compatibility trap:** 7.10.1 changed the signature to `will_set(topic, msg, retain=False, qos=0)`. The second parameter is renamed and `qos` and `retain` swap places ([PR #221][pr221]; [8.1.0 L284-349][mm8-will]).
  - Call it as `will_set(topic, "offline", retain=True)`, with topic and payload positional and `retain` by keyword. That works on 7.10.0 and on 8.x.
- **Client ID matters for the will:**
  - Neither the blinds nor the meters pass `client_id` ([blinds `code.py` L98-104](https://github.com/heikkileivo/circuitpython-ha-blinds/blob/cd8c9cc/devices/blinds/code.py#L98-L104), [water `mqtt.py` L86-95](https://github.com/heikkileivo/circuitpython-ha-blinds/blob/cd8c9cc/devices/water/mqtt.py#L86-L95)). So every `MQTT()` object gets a new random `cpy<rand><rand>` ID ([L210-221][mm0-cid]).
  - With a **stable** ID, the broker must disconnect the old connection when the same ID connects again ([MQTT-3.1.4-2][spec-conn]). Mosquitto then publishes the old session's will at once, as part of handling the new CONNECT, when clean-session is set ([mosquitto L120-212][mosq]). The new session's retained "online" follows it.
  - With a **random** ID, nothing takes the old session over. A half-open old session lingers until the 90 s keep-alive expiry and *then* publishes its retained "offline", possibly **after** the new session has said "online". HA would then show the device as unavailable while it works.
  - So pass `client_id=<device_id>`.
  - *Check:* the Mosquitto version in the HA add-on. The source cited here is 2.0.22.

---

## 4. Stale socket after a socket error

### What 7.10.0 does

1. An error in `loop()`'s `_wait_for_msg()` raises `MMQTTException` or `OSError` (section 1). An error in `publish()` raises the `OSError` from `send()`. Neither path closes the socket or clears `_is_connected` ([L962-1031][mm0-wfm1], [L610-696][mm0-pubts]). `is_connected()` keeps returning True ([L1152-1156][mm0-isc]).
2. ConnectionManager keeps the socket registered under the key `(host, port, "mqtt:", None)` ([CM L314][cm-get]). Its `get_socket()` returns a registered socket only if it has been marked *available*; otherwise it raises `RuntimeError("An existing socket is already connected to mqtt://…")` ([CM L316-325][cm-get]).
3. `connect()` catches that `RuntimeError` as a socket error, without closing anything and without backoff. It retries 5 times, then raises `MMQTTException("Repeated connect failures")` ([L388-435][mm0-connect]).
4. `disconnect()` would close the socket, but it wraps the DISCONNECT `send()` only in `except RuntimeError` ([L575-579][mm0-disc]). CircuitPython's `send()` on a broken socket raises **`OSError`** ([CP L586-602][cps-send]). The exception escapes before `_close_socket()` runs, so the registration survives an explicit `disconnect()` too.
   - The upstream PR that fixed this in 7.10.4 describes exactly these two failures ([PR #224][pr224]).
   - The upstream bug report shows the same log line the blinds would print ([MiniMQTT #222][i222]). There the maintainer suggested calling `_close_socket()` before `reconnect()`.

### What releases the registration

| Call | Effect | Same pool works afterwards? |
|---|---|---|
| `adafruit_connection_manager.connection_manager_close_all(pool)` | Looks up the pool's manager and runs `_free_sockets(force=True)`. That calls `close_socket()` on every managed socket, which calls `socket.close()` and deletes both registry entries ([CM L356-377][cm-closeall], [L212-220][cm-free], [L267-279][cm-close]). | **Yes.** The next `get_socket()` finds no key and opens a new socket ([CM L316-341][cm-get]). |
| `get_connection_manager(pool).close_socket(client._sock)` | The same, for one socket. It needs the private `client._sock`. `client._close_socket()` does the same and also sets `_sock = None` ([L545-549][mm0-closesock]). | **Yes.** |
| `free_socket(sock)` | Only marks the socket *available* for reuse and leaves it open ([CM L281-285][cm-freesock]). The next `get_socket()` hands back **the same dead socket** ([CM L317-321][cm-get]). | **No.** Don't use it. |
| Fresh `SocketPool` (what the meters do) | `get_connection_manager()` is keyed by the pool object, so a new pool gets an empty manager ([CM L390-396][cm-getcm]). | Works, but see the leak below. |

CircuitPython's `socket.close()` doesn't raise on a dead or already-closed socket: it ignores bad descriptors and sets `num = -1` ([CP L346-371][cps-close]). So `close_all` is safe to call on a broken connection.

### Caveats

- **Don't pass `release_references=True`.** It runs `_global_key_by_socketpool.pop(pool)` with no default ([CM L382][cm-closeall]). That dict is filled only by `get_radio_socketpool()` ([CM L179][cm-radio]). A pool created with `socketpool.SocketPool(wifi.radio)`, as both device types do, therefore raises `KeyError`. This is still true in 3.1.8.
- **Don't reuse the old client after `close_all`.** Its `_sock` still points at the now-unregistered socket, and `_is_connected` is still True.
  - On 7.10.0, `reconnect()` happens to work: `get_socket()` succeeds and replaces `_sock` ([L469][mm0-getsock]).
  - On 7.10.4 and later, if that first `get_socket()` fails (for example, the broker is still down), the new `_close_socket()` in the except path calls `close_socket()` on the old socket. That raises `RuntimeError("Socket not managed")` from inside the handler ([CM L273-274][cm-close], [7.10.4 L436-444][mm4-connect]).
  - Build a new `MQTT()` after `close_all`.
- **The fresh-pool approach leaks the socket if `disconnect()` fails to close it.**
  - When 7.10.0's `disconnect()` raises `OSError` (step 4 above), the old socket stays open. It is still referenced by the old manager in the module-global `_global_connection_managers` dict ([CM L104][cm-globals], [L394-395][cm-getcm]), so its `__del__` finaliser ([CP shared-bindings L399][cpb-del]) never runs.
  - CircuitPython 9.1 builds lwIP with **8 sockets** (`CONFIG_LWIP_MAX_SOCKETS=8`, [sdkconfig L64][sdk-lwip]), and the web-workflow listener uses some of them. When they run out, `socket()` raises `RuntimeError("Out of sockets")` ([CP L237-243][cps-alloc]).
  - Every rebuild also leaves one `SocketPool` and one `ConnectionManager` behind in the global dict, including every failed attempt during a long outage. That is small Python-heap garbage.
  - *Evidence, not proof:* in HA, `sensor.electricity_meter_reconnects` climbed 2 → 6 between Sep 1 and Sep 9 without a reboot. It then reset to 0, meaning the meter rebooted, during the 3.7 h broker outage on Sep 10. The cause of that reboot isn't known. *Hardware check:* force N rebuilds and watch for "Out of sockets".
- **Recommended teardown on 7.10.0:** `try: client.disconnect()` and swallow any error, then `connection_manager_close_all(pool)` (no `release_references`), then drop the client. Keep **one** `SocketPool` for the life of the program. *Hardware check:* that one pool keeps working across a Wi-Fi reconnect.

---

## 5. `on_disconnect`

- In 7.10.0 `on_disconnect` is called in exactly one place, at the end of `disconnect()` ([L583-584][mm0-disc]). `deinit()` and `__exit__` reach it only through `disconnect()` ([L244-254][mm0-deinit]).
- Errors in `loop()`, `publish()`, `ping()` or `connect()` never call it.
- 8.0.0 added one more caller: `reconnect()` calls `disconnect()` first when `is_connected()` ([PR #244][pr244]; [8.1.0 L954-981][mm8-reconn]). On a stale client that fires `on_disconnect` synchronously *inside* `reconnect()`.
- Socket errors still don't fire it in 8.1.0: `_wait_for_msg` just re-raises ([8.1.0 L1039-1045][mm8-wfm]).
- **So the application must treat any exception from `loop()`, `publish()` or `subscribe()` as a disconnect.**

---

## 6. Newer releases

From the [release notes][mm-rel] and diffs between the tags:

| Version | Change relevant here |
|---|---|
| 7.10.1 | `will_set()` signature change (section 3). |
| **7.10.4** | `connect()` calls `_close_socket()` on socket errors, and `disconnect()` catches `OSError` ([7.10.4 L436-444][mm4-connect], [L589-602][mm4-disc]; [PR #224][pr224]). **This fixes the stale-socket lockout:** on a stale client, the first `get_socket()` raises "existing socket", the handler closes the old socket and unregisters it, and the second attempt succeeds. So even the blinds' current catch-and-`reconnect()` code would recover. |
| 7.10.5 | Default `loop(timeout=1.0)` ([PR #228][pr228]). |
| **7.10.6** | All sends go through `_send_bytes()`, which loops until every byte is sent ([PR #231][pr231], fixing [#230][i230] "malformed messages when messages are too large", past about 2.8 KB; [8.1.0 L485-502][mm8-send]). See the next section. |
| 7.11.x | `session_id` for `connect()`, handling of UNSUBACK and PUBLISH ordering, and handling of `send()` that returns no byte count. |
| **8.0.0** | Breaking: many `MMQTTException`s become `ValueError`, `NotImplementedError` or `MMQTTStateError`. `reconnect()` disconnects first if connected ([PR #244][pr244]). |
| 8.1.0 | `publish()` enforces `mqtt_msg` (default limit 10 MB, irrelevant here). |

**Still unfixed in 8.1.0:**

- `loop()` is still blocking, runs for its full timeout, and keeps the inline blocking `ping()` ([L983-1022][mm8-loop], [L649-670][mm8-ping]).
- Errors neither mark the client disconnected nor call `on_disconnect`.
- The FIN spin up to `recv_timeout` is still there ([L1134-1141][mm8-ser]).
- New in 7.10.6 and later: `_send_bytes()` retries `EAGAIN` with **no timeout** ([L499-501][mm8-send]). While lwIP's send buffer is full on a dying link, a `publish()` can busy-spin until lwIP gives up. *Hardware check.*

**Can it run on CircuitPython 9.1.x?**

- 8.1.0 is pure Python and depends only on `adafruit_connection_manager` and `adafruit_ticks`, with no version pins ([requirements.txt at 8.1.0](https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/8.1.0/requirements.txt)).
- The Adafruit bundle release `20260910` still ships a `adafruit-circuitpython-bundle-9.x-mpy` zip. Its index lists `adafruit_minimqtt` 8.1.0, `adafruit_connection_manager` 3.1.8 and `adafruit_ticks` 1.1.7 ([bundle release 20260910](https://github.com/adafruit/Adafruit_CircuitPython_Bundle/releases/tag/20260910)).
- It uses `ConnectionManager.get_socket(session_id=…)`, which 3.1.1 already supports ([CM L288-298][cm-get]).
- So it can be installed. Whether it imports and fits in RAM on the devices is a *hardware check*; the TinyS3 has PSRAM, so size is unlikely to matter.

---

## Related finding: the ~2.7 KB discovery-payload limit is a 7.10.0 partial-send bug

- CircuitPython 9.1 sets `CONFIG_LWIP_TCP_SND_BUF_DEFAULT=2880` ([sdkconfig L72][sdk-lwip]), and its sockets are non-blocking ([CP L208-209][cps-nb]). `lwip_send` on a non-blocking socket accepts only what fits in the send buffer and returns that count ([CP L576-593][cps-send]).
- 7.10.0's `publish()` calls `self._sock.send(msg)` and ignores the return value ([L671-673][mm0-pubts]). A PUBLISH larger than the free send buffer is silently cut short. The broker sees a malformed packet and drops the connection.
- That matches both this project's observed limit of about 2.7 KB and upstream [#230][i230] ("once you get past 2.833KB"). The fix, [PR #231][pr231], was "tested by sending 2900-byte packets".
- Either keep the discovery payload under about 2.7 KB, or upgrade to 7.10.6 or later.

---

## Implications for the blinds' MQTT rework

This is the shared `Mqtt` supervisor with short idle `loop()` calls, and servicing MQTT while the blinds move.

1. **Flag disconnects yourself.** `Mqtt.loop()` must catch *every* exception from `client.loop()` and set `on_disconnected`, the same way `publish()` already does. `on_disconnect` won't fire for errors (section 5), and after a mid-packet error the stream is out of sync (section 1).
2. **Tear down explicitly and keep one pool.** On rebuild, run `try: client.disconnect()` and swallow any error, then `connection_manager_close_all(self.pool)`, then create a new `MQTT()` on the **same** pool.
   - Stop creating a new `SocketPool` per rebuild. That is a latent socket leak in the meters today (section 4).
   - Never use `free_socket()` or `release_references=True`.
3. **Set the will on every new client** before `connect()`: `will_set(avail_topic, "offline", retain=True)`. Pass `client_id=<device_id>` so the broker takes the old session over and publishes its will *before* the new "online".
4. **`loop()` always blocks for its full timeout.** With `socket_timeout=1`, one `loop(1)` freezes asyncio for about 1 s. Calling it every 0.5–1 s keeps the event loop frozen more than half the time, which is the same starvation [Blocking MQTT loop starves asyncio](https://github.com/heikkileivo/circuitpython-ha-blinds/issues/5) describes, just shorter.
   - For servicing MQTT *while moving*, construct with `socket_timeout=0.25` (it can go below 1 s; section 1) and call `loop(0.25)`.
   - Set `recv_timeout` to about 3 s. That bounds the FIN spin and mid-packet stalls to 3 s instead of 10 s, well under the 16 s watchdog.
   - *Hardware check:* connect reliability and actual `loop()` durations. Log `ticks_ms()` around each call, as the planned `SLOW LOOP` diagnostic does.
5. **Publish at least every `keep_alive / 2`, including while moving.** For example, uptime every 10 s. Then `loop()` never enters the blocking `ping()`, which can take about 61 s on a silent link, and the broker's 90 s keep-alive stays satisfied (section 2).
6. **Detect a silent dead broker another way.** Successful publishes don't prove the link is alive (section 2).
   - Option A, public API only: subscribe to the device's own `uptime_seconds` state topic and rebuild if no echo arrives for about 30–45 s.
   - Option B, private API: send `b"\xc0\x00"` (PINGREQ) on `client._sock` yourself and look for `0xD0` in the list `loop()` returns ([L953-955][mm0-loopbody], [L989-994][mm0-wfm1]). It never blocks.
   - The meters' `STALL_TIMEOUT` has the same blind spot.
7. **Library version.** Pinning 7.10.0 works if you follow points 1–6.
   - Upgrading to 8.1.0 from the 9.x bundle adds safety nets: `reconnect()` self-heals stale sockets (7.10.4), publishes larger than 2.8 KB work (7.10.6), and `disconnect()` closes the socket even on `OSError`.
   - It changes exception types, the `will_set` parameter order, and makes `reconnect()` fire `on_disconnect`.
   - It doesn't remove the need for points 1, 4, 5 and 6.
   - Whichever version you pick, write the teardown so it doesn't depend on version-specific `reconnect()` behaviour: always build a new client.

---

## Needs hardware confirmation

- Actual duration of an idle `loop(1)` and `loop(0.25)` on the TinyS3, and whether a sub-second `socket_timeout` connects reliably over Wi-Fi.
- Broker stopped cleanly (FIN): whether `loop()` spins for `recv_timeout` and then raises, or raises `OSError(ETIMEDOUT)` after `socket_timeout`. This depends on whether lwIP keeps returning 0 after FIN.
- Broker host powered off (silent): how long until `publish()` raises, which depends on lwIP retransmit give-up. Also how long `_send_bytes` spins on 7.10.6 and later.
- Whether the fresh-pool rebuild leaks lwIP sockets until "Out of sockets", and whether a single `SocketPool` survives Wi-Fi reconnects.
- That MiniMQTT 8.1.0 and ConnectionManager 3.1.8 from the 9.x bundle import and run on CP 9.1.1 / 9.1.3.
- The Mosquitto version in the HA add-on, and its will-on-takeover behaviour. The source cited here is 2.0.22.

<!-- MiniMQTT 7.10.0 -->
[mm0]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/7.10.0/adafruit_minimqtt/adafruit_minimqtt.py
[mm0-init]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/7.10.0/adafruit_minimqtt/adafruit_minimqtt.py#L141-L158
[mm0-rt]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/7.10.0/adafruit_minimqtt/adafruit_minimqtt.py#L166-L169
[mm0-cid]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/7.10.0/adafruit_minimqtt/adafruit_minimqtt.py#L210-L221
[mm0-lwinit]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/7.10.0/adafruit_minimqtt/adafruit_minimqtt.py#L223-L227
[mm0-deinit]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/7.10.0/adafruit_minimqtt/adafruit_minimqtt.py#L244-L254
[mm0-will]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/7.10.0/adafruit_minimqtt/adafruit_minimqtt.py#L270-L303
[mm0-connect]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/7.10.0/adafruit_minimqtt/adafruit_minimqtt.py#L388-L435
[mm0-getsock]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/7.10.0/adafruit_minimqtt/adafruit_minimqtt.py#L468-L476
[mm0-willenc]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/7.10.0/adafruit_minimqtt/adafruit_minimqtt.py#L500-L505
[mm0-willsend]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/7.10.0/adafruit_minimqtt/adafruit_minimqtt.py#L511-L522
[mm0-connts]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/7.10.0/adafruit_minimqtt/adafruit_minimqtt.py#L522-L543
[mm0-closesock]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/7.10.0/adafruit_minimqtt/adafruit_minimqtt.py#L545-L549
[mm0-disc]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/7.10.0/adafruit_minimqtt/adafruit_minimqtt.py#L571-L584
[mm0-ping]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/7.10.0/adafruit_minimqtt/adafruit_minimqtt.py#L586-L607
[mm0-pubts]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/7.10.0/adafruit_minimqtt/adafruit_minimqtt.py#L610-L696
[mm0-subts]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/7.10.0/adafruit_minimqtt/adafruit_minimqtt.py#L750-L752
[mm0-unsubts]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/7.10.0/adafruit_minimqtt/adafruit_minimqtt.py#L827-L828
[mm0-reconn]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/7.10.0/adafruit_minimqtt/adafruit_minimqtt.py#L893-L915
[mm0-loopchk]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/7.10.0/adafruit_minimqtt/adafruit_minimqtt.py#L917-L929
[mm0-loopbody]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/7.10.0/adafruit_minimqtt/adafruit_minimqtt.py#L934-L960
[mm0-wfm1]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/7.10.0/adafruit_minimqtt/adafruit_minimqtt.py#L962-L997
[mm0-wfm2]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/7.10.0/adafruit_minimqtt/adafruit_minimqtt.py#L999-L1031
[mm0-onmsg]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/7.10.0/adafruit_minimqtt/adafruit_minimqtt.py#L1019-L1023
[mm0-ser]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/7.10.0/adafruit_minimqtt/adafruit_minimqtt.py#L1046-L1080
[mm0-isc]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/7.10.0/adafruit_minimqtt/adafruit_minimqtt.py#L1145-L1156
<!-- MiniMQTT 7.10.4 / 8.1.0 -->
[mm4]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/7.10.4/adafruit_minimqtt/adafruit_minimqtt.py
[mm4-connect]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/7.10.4/adafruit_minimqtt/adafruit_minimqtt.py#L436-L444
[mm4-disc]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/7.10.4/adafruit_minimqtt/adafruit_minimqtt.py#L589-L602
[mm8]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/8.1.0/adafruit_minimqtt/adafruit_minimqtt.py
[mm8-will]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/8.1.0/adafruit_minimqtt/adafruit_minimqtt.py#L284-L349
[mm8-send]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/8.1.0/adafruit_minimqtt/adafruit_minimqtt.py#L485-L502
[mm8-ping]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/8.1.0/adafruit_minimqtt/adafruit_minimqtt.py#L649-L670
[mm8-reconn]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/8.1.0/adafruit_minimqtt/adafruit_minimqtt.py#L954-L981
[mm8-loop]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/8.1.0/adafruit_minimqtt/adafruit_minimqtt.py#L983-L1022
[mm8-wfm]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/8.1.0/adafruit_minimqtt/adafruit_minimqtt.py#L1039-L1045
[mm8-ser]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/8.1.0/adafruit_minimqtt/adafruit_minimqtt.py#L1134-L1141
[mm-rel]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/releases
[pr221]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/pull/221
[pr224]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/pull/224
[pr228]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/pull/228
[pr231]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/pull/231
[pr244]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/pull/244
[i86]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/issues/86
[i222]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/issues/222
[i230]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/issues/230
<!-- ConnectionManager 3.1.1 -->
[cm]: https://github.com/adafruit/Adafruit_CircuitPython_ConnectionManager/blob/3.1.1/adafruit_connection_manager.py
[cm-globals]: https://github.com/adafruit/Adafruit_CircuitPython_ConnectionManager/blob/3.1.1/adafruit_connection_manager.py#L104-L107
[cm-radio]: https://github.com/adafruit/Adafruit_CircuitPython_ConnectionManager/blob/3.1.1/adafruit_connection_manager.py#L179-L183
[cm-free]: https://github.com/adafruit/Adafruit_CircuitPython_ConnectionManager/blob/3.1.1/adafruit_connection_manager.py#L212-L220
[cm-gcs]: https://github.com/adafruit/Adafruit_CircuitPython_ConnectionManager/blob/3.1.1/adafruit_connection_manager.py#L245-L253
[cm-close]: https://github.com/adafruit/Adafruit_CircuitPython_ConnectionManager/blob/3.1.1/adafruit_connection_manager.py#L267-L279
[cm-freesock]: https://github.com/adafruit/Adafruit_CircuitPython_ConnectionManager/blob/3.1.1/adafruit_connection_manager.py#L281-L285
[cm-get]: https://github.com/adafruit/Adafruit_CircuitPython_ConnectionManager/blob/3.1.1/adafruit_connection_manager.py#L288-L353
[cm-closeall]: https://github.com/adafruit/Adafruit_CircuitPython_ConnectionManager/blob/3.1.1/adafruit_connection_manager.py#L356-L387
[cm-getcm]: https://github.com/adafruit/Adafruit_CircuitPython_ConnectionManager/blob/3.1.1/adafruit_connection_manager.py#L390-L396
<!-- CircuitPython 9.1.3 (identical at 9.1.1) -->
[cps]: https://github.com/adafruit/circuitpython/blob/9.1.3/ports/espressif/common-hal/socketpool/Socket.c
[cps-poll]: https://github.com/adafruit/circuitpython/blob/9.1.3/ports/espressif/common-hal/socketpool/Socket.c#L42
[cps-nb]: https://github.com/adafruit/circuitpython/blob/9.1.3/ports/espressif/common-hal/socketpool/Socket.c#L200-L211
[cps-alloc]: https://github.com/adafruit/circuitpython/blob/9.1.3/ports/espressif/common-hal/socketpool/Socket.c#L231-L245
[cps-close]: https://github.com/adafruit/circuitpython/blob/9.1.3/ports/espressif/common-hal/socketpool/Socket.c#L346-L371
[cps-connect]: https://github.com/adafruit/circuitpython/blob/9.1.3/ports/espressif/common-hal/socketpool/Socket.c#L402-L471
[cps-recv]: https://github.com/adafruit/circuitpython/blob/9.1.3/ports/espressif/common-hal/socketpool/Socket.c#L525-L574
[cps-send]: https://github.com/adafruit/circuitpython/blob/9.1.3/ports/espressif/common-hal/socketpool/Socket.c#L576-L603
[cpb]: https://github.com/adafruit/circuitpython/blob/9.1.3/shared-bindings/socketpool/Socket.c
[cpb-st]: https://github.com/adafruit/circuitpython/blob/9.1.3/shared-bindings/socketpool/Socket.c#L361-L381
[cpb-del]: https://github.com/adafruit/circuitpython/blob/9.1.3/shared-bindings/socketpool/Socket.c#L396-L399
[sdk]: https://github.com/adafruit/circuitpython/blob/9.1.3/ports/espressif/esp-idf-config/sdkconfig.defaults
[sdk-lwip]: https://github.com/adafruit/circuitpython/blob/9.1.3/ports/espressif/esp-idf-config/sdkconfig.defaults#L62-L77
[tinys3]: https://github.com/adafruit/circuitpython/blob/9.1.3/ports/espressif/boards/unexpectedmaker_tinys3/sdkconfig
<!-- MQTT 3.1.1 spec and Mosquitto -->
[spec]: https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html
[spec-wf]: https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html#_Toc385349232
[spec-wr]: https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html#_Toc385349234
[spec-ka]: https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html#_Toc385349237
[spec-conn]: https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html#_Toc398718032
[spec-disc]: https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html#_Toc398718094
[mosq]: https://github.com/eclipse-mosquitto/mosquitto/blob/v2.0.22/src/handle_connect.c#L120-L212
