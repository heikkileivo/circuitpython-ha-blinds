# HA device discovery: shared options, abbreviations, availability, and the payload size limit

Research for [Research: HA device-discovery shared options and the payload size limit](https://github.com/heikkileivo/circuitpython-ha-blinds/issues/16), part of [Blinds reliability: plan for the open issues](https://github.com/heikkileivo/circuitpython-ha-blinds/issues/12). Written 2026-09-11.

Every claim links to a tag- or commit-pinned primary source. The pinned versions are:

| Component | Version read | Why this one |
|---|---|---|
| Home Assistant core | tag `2026.9.1` | latest release |
| HA docs (`home-assistant.io`) | commit `21a3823` | `current` branch head on 2026-09-11 |
| Adafruit MiniMQTT | tags `7.10.0` (on the devices), `7.10.6`, `7.11.0`, `7.11.6`, `8.1.0` | device version plus the fix history |
| CircuitPython | tags `9.1.1` / `9.1.3` (on the devices) | espressif socket and sdkconfig |
| ESP-IDF / lwIP | `adafruit/esp-idf@d58f9ce`, `espressif/esp-lwip@f792214` | submodules pinned by CircuitPython 9.1.3 |
| Mosquitto | tags `v2.0.22` and `v2.1.2` | the log wording fits 2.0.x in May; today's add-on logs use the 2.1.x wording |
| HA Mosquitto add-on | `home-assistant/addons@730fc0b` | broker config template |

## Summary

1. **Shared options.** You can set these once at the device root: `availability`, `availability_mode`, `availability_template`, `availability_topic`, `payload_available`, `payload_not_available`, `command_topic`, `state_topic`, `qos`, `encoding` and `message_expiry_interval`. A component inherits a root option only if it doesn't set the option itself. `~` (base topic) and `json_attributes_topic` are **not** shared.
2. **Abbreviations.** HA accepts the same key abbreviations at the root and inside components. `dev`, `o` and the list entries under `availability` have their own tables. The table in §2 maps every key this repo uses.
3. **JSON attributes.** Point `json_attributes_topic` at a topic that carries a JSON object. Each message replaces the sensor's whole attribute set. It may be the sensor's own state topic, with a `value_template` that picks out the state.
4. **Availability.** One root `availability_topic` covers every entity. The payloads default to `online` and `offline`. An entity starts **unavailable** until HA sees `online`, so publish both the will and `online` with retain. Retained state still loads while the entity is unavailable. There's one trap: a stale session's will can land *after* the new `online` unless the client ID is fixed.
5. **The size limit.** It's a MiniMQTT 7.10.0 bug. `publish()` calls `socket.send()` once and ignores the byte count. CircuitPython's ESP32 sockets are non-blocking, and the lwIP send buffer is **2,880 bytes**, so a larger PUBLISH is cut short and the stream falls out of sync. It isn't HA, the remaining-length encoding, or a Mosquitto limit. MiniMQTT **7.11.0** or later fixes it. 7.10.6 has a partial fix, but it crashes with `NameError` on the ESP32. Until then, the most a PUBLISH payload can hold is about **2,828 B** for the blinds (2,823 B for the electricity meter), and less if earlier bytes are still unacknowledged.

---

## 1. Shared options at the device root

HA's schema module defines the list of shared options ([`schemas.py` L64-L76][schemas-shared]):

```python
SHARED_OPTIONS = [
    CONF_AVAILABILITY, CONF_AVAILABILITY_MODE, CONF_AVAILABILITY_TEMPLATE,
    CONF_AVAILABILITY_TOPIC, CONF_COMMAND_TOPIC, CONF_ENCODING,
    CONF_MESSAGE_EXPIRY_INTERVAL, CONF_PAYLOAD_AVAILABLE,
    CONF_PAYLOAD_NOT_AVAILABLE, CONF_STATE_TOPIC, CONF_QOS,
]
```

- **Merge rule.** `_merge_common_device_options` copies each shared option from the root into a component *only if the component doesn't set it* ([`discovery.py` L335-L357][disc-merge]). It runs for every component in the `cmps` map ([L446-L454][disc-loop]).
- **The root schema is closed.** `DEVICE_DISCOVERY_SCHEMA` allows the availability keys plus `device`, `components` and `origin`, which are required, and `state_topic`, `command_topic`, `message_expiry_interval`, `qos` and `encoding` ([`schemas.py` L212-L223][schemas-device]). It builds on a plain `vol.Schema`, so any other root key fails validation with "extra keys not allowed". HA then logs `Invalid MQTT device discovery payload` and drops the **whole** message ([`discovery.py` L303-L313][disc-parse]). I checked the extend behaviour by rebuilding the same `vol.Schema(...).extend(...)` pattern locally; voluptuous rejected `{"~": ...}` at the root.
- **So `~` must go inside each component.** The base-topic swap runs per component after the merge ([`discovery.py` L500-L501][disc-tilde], [L224-L243][disc-replace-tilde]). Component configs accept extra keys ([`schemas.py` L205-L210][schemas-component]).
- **The docs list is slightly shorter than the code.** The docs name availability, origin, `command_topic`, `state_topic`, `qos` and `encoding` ([docs L390-L404][docs-device]). The code also shares `message_expiry_interval`.
- **Required per component:** `platform` (`p`), and `unique_id` for entity platforms ([`schemas.py` L197-L210][schemas-component]; [docs L449][docs-cmps]). `device` and `origin` are required at the root and can't be overridden per component ([docs L390-L395][docs-device]).
- **Inherited keys a platform doesn't use are harmless.** Platform discovery schemas drop unknown keys, for example sensor ([`sensor.py` L165-L166][sensor-schema]) and cover ([`cover.py` L210-L211][cover-schema]). A root `command_topic` is therefore ignored by sensors.

**What this means for the repo.** `HADiscovery.add_component()` fills in `state_topic` for every component ([`shared/discovery.py`][repo-discovery]). A root `state_topic` would therefore never be inherited. A shared state topic only pays off if every entity reads one JSON state message through its own `value_template`. That saves little (see the savings table), and all entities would then update together. For this repo, **availability** is the shared option that pays off: 58 B at the root against 348 B if repeated in all six components.

## 2. Abbreviations

`_replace_all_abbreviations` expands abbreviations in this order ([`discovery.py` L188-L221][disc-abbr]):

- at the root, with the `ABBREVIATIONS` table, which is how `cmps`, `dev`, `o`, `avty_t`, `stat_t` and friends work at the top level
- inside each entry of an `availability` list, with the same table (`t` → `topic`)
- inside `dev`, with `DEVICE_ABBREVIATIONS`, and inside `o`, with `ORIGIN_ABBREVIATIONS`
- inside each component, with `ABBREVIATIONS` again, including the component's `availability` entries

The component pass uses `component_only=True`, so a `dev` or `o` block *inside* a component isn't expanded. That doesn't matter for device discovery. The tables are in [`abbreviations.py`][abbr] ([`DEVICE_ABBREVIATIONS` L279][abbr-dev], [`ORIGIN_ABBREVIATIONS` L293][abbr-origin]). The docs publish the same list ([docs L732][docs-abbr]).

The keys this repo emits (blinds, water and electricity `code.py`, plus `shared/discovery.py`):

| Full key | Abbreviation | Where |
|---|---|---|
| `platform` | `p` (already used) | component |
| `unique_id` | `uniq_id` | component |
| `state_topic` | `stat_t` | component, or root as a shared option |
| `command_topic` | `cmd_t` | component, or root |
| `device_class` | `dev_cla` | component |
| `entity_category` | `ent_cat` | component |
| `state_class` | `stat_cla` | component |
| `unit_of_measurement` | `unit_of_meas` | component |
| `suggested_display_precision` | `sug_dsp_prc` | component (water) |
| `tilt_status_topic` | `tilt_status_t` | cover |
| `tilt_command_topic` | `tilt_cmd_t` | cover |
| `payload_open` / `payload_close` / `payload_stop` | `pl_open` / `pl_cls` / `pl_stop` | cover |
| `name`, `min`, `max`, `step`, `tilt_min`, `tilt_max` | no shorter form (they map to themselves) | component |
| `components` | `cmps` (already used) | root |
| `device` / `origin` | `dev` / `o` (already used) | root |
| `identifiers` | `ids` (already used) | inside `dev` |
| `manufacturer` / `model` | `mf` / `mdl` | inside `dev` |
| new: `availability_topic` | `avty_t` | root |
| new: `availability` list, `topic` | `avty`, `t` | root, list entries |
| new: `payload_available` / `payload_not_available` | `pl_avail` / `pl_not_avail` | root |
| new: `json_attributes_topic` / `json_attributes_template` | `json_attr_t` / `json_attr_tpl` | component |
| new: `value_template` | `val_tpl` | component |
| new: `expire_after` | `exp_aft` | component |
| `qos` | none | root or component |

Changing `shared/discovery.py` to emit abbreviations changes the meters' payloads too. They stay valid because the expansion runs for all device discovery.

## 3. `json_attributes_topic` on a sensor

- **Subscription.** `MqttAttributesMixin` subscribes to `json_attributes_topic` with the entity's `qos` and `encoding` ([`entity.py` L519-L548][entity-attr-sub]). Those two are shared options, so the root values apply.
- **Handling.** Each message is run through `json_attributes_template`, if one is set, and then parsed. If the result is a JSON **object**, it *replaces* the entity's extra attributes wholesale; nothing is merged. Anything else (not JSON, or JSON that isn't an object) logs `Erroneous JSON` or is ignored ([`entity.py` L563-L584][entity-attr-recv]). Every attribute goes into one message, and a key that's missing from a message disappears from the entity.
- **Blocked keys.** Some keys are filtered out, for example `state`, `unique_id`, `device_class`, `unit_of_measurement`, `friendly_name`, `icon` and `available` ([`entity.py` L137-L156][entity-blocked]). Don't use those names for diagnostics.
- **Sharing the state topic.** The attributes topic can be the sensor's own `state_topic`. HA then writes one state per message, "unless the state update did not change the state or `force_update` was set" ([sensor docs L375][sensor-docs-shared]). The docs also say that `json_attributes_topic` "implies `force_update` of the current sensor state when a message is received on this topic" ([sensor docs L167-L168][sensor-docs-attr]). They also discourage attributes that change on every update, because each change is a state write ([sensor docs L377][sensor-docs-perf]).
- **No long-term statistics for attributes.** Statistics are kept for a sensor's *state*, and only when `state_class` is set ([developer docs, sensor "Long-term Statistics"][dev-sensor-stats]). If a diagnostic must be graphed over months, such as the minimum supply voltage, give it its own sensor. The rest can be attributes.

A servo-diagnostics sensor in the compact form costs about **250-290 B**. Four separate sensors cost about **1,185 B** (host measurement below):

```json
"<did>_servo": {
  "p": "sensor", "uniq_id": "<did>_servo", "name": "Servo", "ent_cat": "diagnostic",
  "stat_t": "<did>/servo/state", "val_tpl": "{{ value_json.status }}",
  "json_attr_t": "<did>/servo/state"
}
```

The device would publish `{"status": "ok", "v_min": 7.4, "temp": 41, "load_peak": 63, "uart_err": 0}` to `<did>/servo/state`. The state is the `status` string, and the rest become attributes. Without the template, a separate attributes topic is slightly smaller: `stat_t` points at a plain value and `json_attr_t` at `<did>/servo/attr`.

## 4. Availability in device discovery

- **One topic for every entity.** Put `avty_t: "<did>/availability"` at the root. Every component inherits it, along with `payload_available` / `payload_not_available` if those are set ([§1][schemas-shared]).
- **Defaults.** `payload_available` = `online` and `payload_not_available` = `offline` ([`const.py` L297-L301][const-avail]; [`schemas.py` L79-L90][schemas-avail]; [docs L1077-L1136][docs-avail]). `availability_mode` defaults to `latest`, which only matters for an `availability` *list*.
- **Entities start unavailable.** `_available_latest` starts as `False` ([`entity.py` L601][entity-avail-init]). It only turns `True` when a message equals `payload_available` ([L682-L694][entity-avail-recv]), and `available` returns it ([L717-L729][entity-avail-prop]). After an HA restart, every entity with an availability topic shows `unavailable` until `online` arrives. That's why `online`, and the will's `offline`, must be **retained**. The docs recommend publishing availability as a best practice ([docs L1030-L1043][docs-disc-avail]).
- **HA's own connection counts too.** If HA's MQTT client is disconnected, every MQTT entity is unavailable ([`entity.py` L719-L722][entity-avail-prop]).
- **Retained state.** Availability and state are separate subscriptions. Once the config is processed, HA subscribes to the state topics and any retained state message is replayed ([docs L1058-L1062][docs-retained-state]). While `available` is `False`, HA writes the state as `unavailable` ([`helpers/entity.py` L1063-L1066][ha-entity-stringify]). The value it received is still held and shows as soon as `online` arrives. The availability message triggers a state write, because the callback tracks the `available` attribute ([`entity.py` L660-L666][entity-avail-sub]). The two fixes from [HA can't tell when a blind is offline](https://github.com/heikkileivo/circuitpython-ha-blinds/issues/7) (retained availability and retained cover/tilt/speed state) are independent and work together.
- **When the broker publishes the will.** MQTT 3.1.1 publishes the will when the connection drops without a DISCONNECT, including a keep-alive timeout of 1.5 × `keep_alive` ([MQTT 3.1.1 §3.1.2.5][mqtt-will], [§3.1.2.10][mqtt-keepalive]). MiniMQTT's default `keep_alive` is 60 s ([7.10.0 L150][mm-init]), so a dead blind turns `offline` after up to ~90 s. A clean `disconnect()` sends DISCONNECT, and then **no** will is published. Publish `offline` yourself before any deliberate disconnect.
- **Trap: a stale session's will can override a fresh `online`.** MiniMQTT picks a random `client_id` (`cpyNNNNN`) unless one is given ([7.10.0 L211-L221][mm-clientid]). The blinds don't pass one ([`devices/blinds/code.py`][repo-blinds]).
  - **Different client ID** (the meters rebuild their client; any watchdog reset): the old half-open session lives on at the broker until its keep-alive runs out. Its retained `offline` can then land **after** the new connection's retained `online`, and HA shows a working device as unavailable.
  - **Same client ID** (the MQTT 3.1.1 session takeover, [§3.1.4][mqtt-takeover]): Mosquitto publishes the old session's will *during* the new CONNECT, before CONNACK, when clean session is set ([`handle_connect.c` L193-L212][mosq-takeover]). MiniMQTT's `connect()` defaults to `clean_session=True` ([7.10.0 L372][mm-connect]). The new `online` then always wins.
  - **Recommendation:** pass a fixed `client_id` such as the device ID. `will_set()` must be called before `connect()` and supports `retain` ([7.10.0 L270-L303][mm-will]). Whether it survives `reconnect()` and client rebuilds is covered by [Research: MiniMQTT 7.10 loop timing, last will and stale-socket release](https://github.com/heikkileivo/circuitpython-ha-blinds/issues/15).

## 5. Where the ~2.9 KB limit comes from

### The mechanism

1. **MiniMQTT 7.10.0 sends each PUBLISH with three bare `send()` calls** (fixed header, variable header, payload) and never checks how many bytes went out ([7.10.0 L671-L673][mm-publish-send]). The same pattern is used for every packet type (CONNECT, SUBSCRIBE, `_send_str`).
2. **CircuitPython's ESP32 `socket.send()` can send fewer bytes than asked.** Every socketpool socket is set to `O_NONBLOCK` ([CP 9.1.3 `Socket.c` L208-L209][cp-nonblock]). `send()` makes a single `lwip_send(..., 0)` call and returns its count ([L576-L603][cp-send]). The binding returns that count to Python ([shared-bindings `Socket.c` L223-L239][cp-send-binding]). The same code is in 9.1.1 ([L209, L581][cp-911-socket]).
3. **lwIP cuts a non-blocking write at the free send buffer.** `lwip_send` calls `netconn_write_partly` and returns `written` ([esp-lwip `sockets.c` L1388-L1424][lwip-send]). In `lwip_netconn_do_writemore`, a non-blocking write is limited to `tcp_sndbuf()`. It reports a partial write as success, and `ERR_WOULDBLOCK` only when nothing fit ([`api_msg.c` L1742][lwip-dontblock], [L1767-L1780][lwip-trunc]).
4. **CircuitPython sets the ESP32 TCP send buffer to 2,880 B.** `CONFIG_LWIP_TCP_SND_BUF_DEFAULT=2880` in the port's sdkconfig ([CP 9.1.3 L72][cp-sdkconfig], same in [9.1.1][cp-911-sdkconfig]). That feeds `TCP_SND_BUF` ([ESP-IDF `lwipopts.h` L598][idf-lwipopts]), and each new PCB starts with that much space ([`tcp.c` L1903][lwip-tcp-sndbuf]).
5. **Result:** when the header plus payload exceed the free buffer, the payload `send()` returns a short count and the rest is silently dropped. The broker has read a remaining length of about 3,080 but got about 2,825 payload bytes, so it keeps reading. It takes the **next** packet (the SUBSCRIBE) as more payload. No SUBACK comes back, and MiniMQTT's `subscribe()` gives up after `recv_timeout`, which defaults to **10 s** ([7.10.0 L151][mm-init], [L753-L759][mm-sub-wait]). That matches the device's `MMQTTException('No data received from broker for 10 seconds.')`. The bytes sent after that complete the stuck PUBLISH, and Mosquitto reads what follows from the middle of a packet. Its framing checks then return `MOSQ_ERR_MALFORMED_PACKET`, for example the remaining-length and fixed-size checks in ([`packet_mosq.c` L437-L503][mosq-read]) or the PUBLISH header checks ([`handle_publish.c` L66-L77][mosq-pub-checks]). Mosquitto logs `Client … disconnected due to malformed packet` ([`loop.c` L327-L328][mosq-log-malformed]) and drops the connection, and every publish after that fails with `ECONNRESET`/`BrokenPipe`.

**Upstream confirmation.** [MiniMQTT #230](https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/issues/230) ("MiniMQTT sends malformed messages when messages are too large") reports the same failure past about 2.83 KB on a Pico 2 W. It was fixed by [PR #231 "handle partial socket send()'s"](https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/pull/231) (merge [`75f3845`][mm-pr231-commit]), which says: *"The library was using `socket.send()` directly, but not checking the return value … for larger packets, a partial send would go unnoticed."*

### The numbers match the measured points

The PUBLISH header is 1 byte of type, 2 bytes of remaining length (the packet is under 16 KB), 2 bytes of topic length, and the topic itself.

| Device | Discovery topic | Header bytes | Largest payload in 2,880 B | Measured |
|---|---|---|---|---|
| blinds | `homeassistant/device/blinds_<12 hex>/config` (47 B) | 52 | **2,828 B** | 1,892 B today, works |
| water | `homeassistant/device/water_<12 hex>/config` (46 B) | 51 | 2,829 B | 2,671 B worked |
| electricity | `homeassistant/device/electricity_<12 hex>/config` (52 B) | 57 | **2,823 B** | 3,027 B failed; 2,497 B and ~2,310 B worked |

The predicted cliff sits between the largest payload that worked and the smallest that failed. These figures assume an **empty** send buffer. Bytes that haven't been acknowledged yet, from a packet sent just before, shrink the room. A publish of 2.7 KB right after another large write can still be cut short, which is a reason to keep a margin (the "keep under ~2.7 KB" rule).

### The other candidates, ruled out

- **Remaining-length encoding.** `_encode_remaining_length` is the MQTT 3.1.1 §2.2.3 algorithm ([7.10.0 L552-L569][mm-remlen]; [MQTT 3.1.1 §2.2.3][mqtt-remlen]). For 3,081 it gives `0x89 0x18`, which is correct. The header is fine; the body is short.
- **Mosquitto `max_packet_size`.** An oversized packet is checked right after the remaining length is read, and the result is `MOSQ_ERR_OVERSIZE_PACKET` ([`packet_mosq.c` L507-L512][mosq-read]). That's logged as *"disconnected due to **oversize packet**"* ([`loop.c` L342-L343][mosq-log-malformed]), not "malformed". The option defaults to no limit ([`mosquitto.conf.5` L627-L648][mosq-man-mps]).
- **Mosquitto `message_size_limit`.** It drops the message but keeps the connection open ([`handle_publish.c` L226-L231][mosq-msl]; [`mosquitto.conf.5` L709-L723][mosq-man-msl]), so it can't cause a disconnect.
- **HA's Mosquitto add-on config** sets neither option ([`mosquitto.gtpl`][addon-conf]). A file dropped into the `customize` folder could, but the wording of the log line already rules both options out.
- **HA itself.** HA is a separate MQTT client. The broker's log names the *device's* client ID, and HA plays no part in how the device's packets are framed. The only thing HA sees is the discovery message. If the stuck PUBLISH was completed with bytes from later packets, that message was stored retained with a corrupt JSON tail, and HA would log `Unable to parse JSON` for it ([`discovery.py` L296-L300][disc-parse]). This is inferred, not observed.
- **The broker version.** The log wording quoted in the ticket (`disconnected due to malformed packet`) is the 2.0.x form ([v2.0.22 `loop.c` L327-L328][mosq-log-malformed]). 2.1.x logs `… [ip:port] disconnected: malformed packet.` ([v2.1.2 `loop.c` L321][mosq-212-log]). Today's add-on log uses the 2.1.x form (`… disconnected: connection closed by client.`), so the broker has been upgraded since May. Neither version limits packet size by default.

### Fix and workarounds

- **Fix: upgrade MiniMQTT to 7.11.0 or later; 7.11.6 is the last 7.x.**
  - 7.10.6 added `_send_bytes()`, which loops until every byte is sent and retries on `EAGAIN` ([7.10.6 L464-L477][mm-7106-send]). But it tests `exc.errno == EAGAIN` with only `import errno` in scope ([L32][mm-7106-import], [L475][mm-7106-send]). On the ESP32, the retry after a short send usually meets a buffer that's still full (the broker hasn't acknowledged anything yet). `lwip_send` then fails with `EWOULDBLOCK` ([`api_msg.c` L1771-L1775][lwip-trunc]), and the retry turns into `NameError`. **7.10.6 and 7.10.7 are not safe here.**
  - 7.11.0 uses `errno.EAGAIN` ([7.11.0 L478][mm-7110-eagain]). 7.11.1 also handles `send()` implementations that don't return a count ([PR #235](https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/pull/235); [7.11.6 L471-L488][mm-7116-send]).
  - 8.0.0 changes the exception types to `ValueError` and `MMQTTStateError` ([release notes](https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/releases/tag/8.0.0)). 8.1.0 adds a publish size limit that defaults to 10 MB (`MQTT_MSG_SZ_LIM`; [8.1.0 L700-L707][mm-810-limit]).
  - Whether a newer MiniMQTT runs on CircuitPython 9.1.x, and what else it changes, is open in [Research: MiniMQTT 7.10 loop timing, last will and stale-socket release](https://github.com/heikkileivo/circuitpython-ha-blinds/issues/15).
  - One side effect: `_send_bytes()` spins on `EAGAIN` with no sleep or timeout. A large publish blocks the asyncio loop until the broker acknowledges the data, which takes milliseconds on a LAN. On a link that dies mid-send it spins until lwIP gives up on the connection, and that can outlast the blinds' 16 s watchdog.
- **Workaround without upgrading: stay under the cliff.** Keep each PUBLISH at or below about 2,700 B and don't queue other large writes right before it. The options are in the table below.
- **Workaround: per-component discovery** (`homeassistant/<platform>/<did>/<key>/config`). Each message is small: 230-480 B each for today's six blinds components, 1.9 KB in total. It isn't a real fix on 7.10.0, though: back-to-back publishes share the same 2,880 B buffer. Once the total sent before the broker's ACKs come back passes the free space, a later message gets cut short the same way. It also means going back from device discovery, which HA supports through `migrate_discovery` ([docs L528-L544][docs-migrate], [rollback L649-L661][docs-rollback]), and more messages at every connect. It isn't worth it compared with upgrading.
- **Stopgap patch.** Wrapping `client._sock` so `send()` loops until done would also work. But `_sock` is private and gets recreated on every connect, so an upgrade is cleaner.

## Byte-saving options

Measured on the host by stubbing `wifi` and running `shared/discovery.py` with the blinds' components from `devices/blinds/code.py`. MAC `f4:12:fa:44:80:00`, device name "Upstairs Living Room Right Blinds". CircuitPython's `json.dumps` defaults to the same `", "` / `": "` separators as CPython ([CP 9.1.3 `modjson.c` L57-L60][cp-json]), so the byte counts carry over. **Baseline:** 1,892 B today, 1,950 B with a root `availability_topic`.

| Option | Saving | HA risk / notes |
|---|---|---|
| Upgrade MiniMQTT to 7.11.0 or later | removes the cliff | Needs the compatibility check in [Research: MiniMQTT 7.10 loop timing, last will and stale-socket release](https://github.com/heikkileivo/circuitpython-ha-blinds/issues/15) |
| Compact JSON: `json.dumps(p, separators=(",", ":"))` | **−112 B** (~6%) | None. `separators` is supported on CircuitPython 9.1.3 ([`modjson.c` L39-L65][cp-json]; enabled by default in [`mpconfig.h` L1645-L1648][cp-mpconfig]) |
| Key abbreviations in `discovery.py` | **−174 B** | None; the meters change too and stay valid |
| Compact JSON + abbreviations | −286 B → 1,664 B | |
| Availability at the root, not per component | costs 58 B instead of 348 B | |
| `~` base topic on the cover only | −47 B | Needs `~` per component. It's a net *loss* on one-topic sensors (−35 B in total if used everywhere) |
| Drop the `/state` suffix from state topics | −42 B | Changes the topic scheme; the device's publishes must change too |
| Shorter `cmps` keys (`cover` instead of `<did>_cover`) | −120 B | **Changes the discovery IDs**, so HA sees components removed and added. Test on one device first; not recommended |
| Drop the `Uptime` string sensor (keep `Uptime seconds`) | −192 B | The entity disappears |
| Drop the `Status LED` switch | −259 B | The meters already dropped theirs (`e763fce`) |
| Servo diagnostics as one sensor with `json_attributes_topic` | costs 247-293 B | Four separate sensors would cost about 1,185 B |
| All encoding-only wins (compact + abbreviations + `~` on cover) | −335 B → **1,615 B** | Plus shorter `cmps` keys: 1,495 B |

## Needs hardware or a live-system check

- **The short send itself.** The mechanism is fully sourced, but nobody has watched `send()` return a short count on a TinyS3. To confirm on one device, patch `client._sock.send` to print its return value and publish about 3,000 B to a test topic. Or upgrade MiniMQTT to 7.11.x and republish the 3,027 B electricity payload, which failed before.
- **Which check Mosquitto tripped.** Which framing check fires depends on the bytes that followed the cut. It could just as well have been `protocol error`. Only a broker debug log (`log_type all`) captured during a failure would show it.
- **A corrupted retained discovery message.** If one was left on the broker, HA would have logged `Unable to parse JSON` for that device on 2026-05-27. That's in the HA and broker logs, not in this repo.
- **The installed library versions.** Check them on each device with `import adafruit_minimqtt.adafruit_minimqtt as m; print(m.__version__)`. Bundle `.mpy` files carry the real version string. The installed Mosquitto add-on version and any `customize` files are on the HA host. The MCP connection used here couldn't read the add-on info (Supervisor returned `Unauthorized`).
- **How HA handles renamed `cmps` keys** (the shorter-keys row above). Test on one blind before rolling it out.

[schemas-shared]: https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/mqtt/schemas.py#L64-L76
[schemas-avail]: https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/mqtt/schemas.py#L79-L90
[schemas-component]: https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/mqtt/schemas.py#L197-L210
[schemas-device]: https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/mqtt/schemas.py#L212-L223
[disc-abbr]: https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/mqtt/discovery.py#L188-L221
[disc-replace-tilde]: https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/mqtt/discovery.py#L224-L243
[disc-parse]: https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/mqtt/discovery.py#L274-L314
[disc-merge]: https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/mqtt/discovery.py#L335-L357
[disc-loop]: https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/mqtt/discovery.py#L431-L454
[disc-tilde]: https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/mqtt/discovery.py#L494-L501
[abbr]: https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/mqtt/abbreviations.py#L3-L277
[abbr-dev]: https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/mqtt/abbreviations.py#L279-L291
[abbr-origin]: https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/mqtt/abbreviations.py#L293-L297
[const-avail]: https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/mqtt/const.py#L297-L301
[entity-blocked]: https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/mqtt/entity.py#L137-L156
[entity-attr-sub]: https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/mqtt/entity.py#L519-L548
[entity-attr-recv]: https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/mqtt/entity.py#L563-L584
[entity-avail-init]: https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/mqtt/entity.py#L597-L602
[entity-avail-sub]: https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/mqtt/entity.py#L654-L679
[entity-avail-recv]: https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/mqtt/entity.py#L681-L694
[entity-avail-prop]: https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/mqtt/entity.py#L715-L729
[sensor-schema]: https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/mqtt/sensor.py#L165-L166
[cover-schema]: https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/mqtt/cover.py#L210-L211
[ha-entity-stringify]: https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/helpers/entity.py#L1063-L1066
[docs-device]: https://github.com/home-assistant/home-assistant.io/blob/21a38231f6d7317a30e5c2117f328ee557c9474b/source/_integrations/mqtt.markdown#L383-L404
[docs-cmps]: https://github.com/home-assistant/home-assistant.io/blob/21a38231f6d7317a30e5c2117f328ee557c9474b/source/_integrations/mqtt.markdown#L449
[docs-migrate]: https://github.com/home-assistant/home-assistant.io/blob/21a38231f6d7317a30e5c2117f328ee557c9474b/source/_integrations/mqtt.markdown#L528-L544
[docs-rollback]: https://github.com/home-assistant/home-assistant.io/blob/21a38231f6d7317a30e5c2117f328ee557c9474b/source/_integrations/mqtt.markdown#L649-L661
[docs-abbr]: https://github.com/home-assistant/home-assistant.io/blob/21a38231f6d7317a30e5c2117f328ee557c9474b/source/_integrations/mqtt.markdown#L732
[docs-disc-avail]: https://github.com/home-assistant/home-assistant.io/blob/21a38231f6d7317a30e5c2117f328ee557c9474b/source/_integrations/mqtt.markdown#L1030-L1043
[docs-retained-state]: https://github.com/home-assistant/home-assistant.io/blob/21a38231f6d7317a30e5c2117f328ee557c9474b/source/_integrations/mqtt.markdown#L1058-L1062
[docs-avail]: https://github.com/home-assistant/home-assistant.io/blob/21a38231f6d7317a30e5c2117f328ee557c9474b/source/_integrations/mqtt.markdown#L1077-L1136
[sensor-docs-attr]: https://github.com/home-assistant/home-assistant.io/blob/21a38231f6d7317a30e5c2117f328ee557c9474b/source/_integrations/sensor.mqtt.markdown#L167-L168
[sensor-docs-shared]: https://github.com/home-assistant/home-assistant.io/blob/21a38231f6d7317a30e5c2117f328ee557c9474b/source/_integrations/sensor.mqtt.markdown#L375
[sensor-docs-perf]: https://github.com/home-assistant/home-assistant.io/blob/21a38231f6d7317a30e5c2117f328ee557c9474b/source/_integrations/sensor.mqtt.markdown#L377
[dev-sensor-stats]: https://github.com/home-assistant/developers.home-assistant/blob/28222e9237008cc9f4077f4437a09cb8342d3aa0/docs/core/entity/sensor.md#L123-L128
[mm-init]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/7.10.0/adafruit_minimqtt/adafruit_minimqtt.py#L141-L160
[mm-clientid]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/7.10.0/adafruit_minimqtt/adafruit_minimqtt.py#L211-L221
[mm-will]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/7.10.0/adafruit_minimqtt/adafruit_minimqtt.py#L270-L303
[mm-connect]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/7.10.0/adafruit_minimqtt/adafruit_minimqtt.py#L370-L376
[mm-remlen]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/7.10.0/adafruit_minimqtt/adafruit_minimqtt.py#L552-L569
[mm-publish-send]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/7.10.0/adafruit_minimqtt/adafruit_minimqtt.py#L671-L673
[mm-sub-wait]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/7.10.0/adafruit_minimqtt/adafruit_minimqtt.py#L750-L759
[mm-pr231-commit]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/commit/75f384545d7590c0d92594cc450c97238d27874b
[mm-7106-import]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/7.10.6/adafruit_minimqtt/adafruit_minimqtt.py#L32
[mm-7106-send]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/7.10.6/adafruit_minimqtt/adafruit_minimqtt.py#L464-L477
[mm-7110-eagain]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/7.11.0/adafruit_minimqtt/adafruit_minimqtt.py#L478
[mm-7116-send]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/7.11.6/adafruit_minimqtt/adafruit_minimqtt.py#L471-L488
[mm-810-limit]: https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/blob/8.1.0/adafruit_minimqtt/adafruit_minimqtt.py#L700-L707
[cp-nonblock]: https://github.com/adafruit/circuitpython/blob/9.1.3/ports/espressif/common-hal/socketpool/Socket.c#L208-L209
[cp-send]: https://github.com/adafruit/circuitpython/blob/9.1.3/ports/espressif/common-hal/socketpool/Socket.c#L576-L603
[cp-send-binding]: https://github.com/adafruit/circuitpython/blob/9.1.3/shared-bindings/socketpool/Socket.c#L223-L239
[cp-911-socket]: https://github.com/adafruit/circuitpython/blob/9.1.1/ports/espressif/common-hal/socketpool/Socket.c#L209
[cp-sdkconfig]: https://github.com/adafruit/circuitpython/blob/9.1.3/ports/espressif/esp-idf-config/sdkconfig.defaults#L72
[cp-911-sdkconfig]: https://github.com/adafruit/circuitpython/blob/9.1.1/ports/espressif/esp-idf-config/sdkconfig.defaults#L72
[cp-json]: https://github.com/adafruit/circuitpython/blob/9.1.3/extmod/modjson.c#L39-L65
[cp-mpconfig]: https://github.com/adafruit/circuitpython/blob/9.1.3/py/mpconfig.h#L1645-L1648
[idf-lwipopts]: https://github.com/adafruit/esp-idf/blob/d58f9ce0b4799e63490917b7bfc1300a10bc1f43/components/lwip/port/include/lwipopts.h#L595-L598
[lwip-send]: https://github.com/espressif/esp-lwip/blob/f79221431fa9042b3572d271d687de66da7560c4/src/api/sockets.c#L1388-L1424
[lwip-dontblock]: https://github.com/espressif/esp-lwip/blob/f79221431fa9042b3572d271d687de66da7560c4/src/api/api_msg.c#L1742
[lwip-trunc]: https://github.com/espressif/esp-lwip/blob/f79221431fa9042b3572d271d687de66da7560c4/src/api/api_msg.c#L1767-L1780
[lwip-tcp-sndbuf]: https://github.com/espressif/esp-lwip/blob/f79221431fa9042b3572d271d687de66da7560c4/src/core/tcp.c#L1903
[mosq-read]: https://github.com/eclipse-mosquitto/mosquitto/blob/v2.0.22/lib/packet_mosq.c#L437-L512
[mosq-pub-checks]: https://github.com/eclipse-mosquitto/mosquitto/blob/v2.0.22/src/handle_publish.c#L66-L105
[mosq-msl]: https://github.com/eclipse-mosquitto/mosquitto/blob/v2.0.22/src/handle_publish.c#L226-L231
[mosq-log-malformed]: https://github.com/eclipse-mosquitto/mosquitto/blob/v2.0.22/src/loop.c#L327-L346
[mosq-takeover]: https://github.com/eclipse-mosquitto/mosquitto/blob/v2.0.22/src/handle_connect.c#L193-L212
[mosq-man-mps]: https://github.com/eclipse-mosquitto/mosquitto/blob/v2.0.22/man/mosquitto.conf.5.xml#L627-L648
[mosq-man-msl]: https://github.com/eclipse-mosquitto/mosquitto/blob/v2.0.22/man/mosquitto.conf.5.xml#L709-L723
[mosq-212-log]: https://github.com/eclipse-mosquitto/mosquitto/blob/v2.1.2/src/loop.c#L321
[addon-conf]: https://github.com/home-assistant/addons/blob/730fc0b72d98d85496bb17a16670dcde551342f9/mosquitto/rootfs/usr/share/tempio/mosquitto.gtpl
[mqtt-remlen]: https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html#_Toc398718023
[mqtt-will]: https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html#_Toc398718031
[mqtt-keepalive]: https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html#_Toc398718030
[mqtt-takeover]: https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html#_Toc398718032
[repo-discovery]: https://github.com/heikkileivo/circuitpython-ha-blinds/blob/cd8c9cc/shared/discovery.py#L20-L35
[repo-blinds]: https://github.com/heikkileivo/circuitpython-ha-blinds/blob/cd8c9cc/devices/blinds/code.py#L98-L104
