import time, gc, os, ssl
import board
import supervisor
import analogio
import wifi, socketpool
import asyncio
import microcontroller
from watchdog import WatchDogMode
from adafruit_datetime import timedelta
from blink import blink, Color, pixel
from mqtt import Mqtt, mqtt_publish
from connect_wifi import wifi_supervisor
from discovery import HADiscovery
from schmitt import AdaptiveSchmitt
import tinys3


_TICKS_PERIOD = 1 << 29
_TICKS_MAX = _TICKS_PERIOD - 1
_TICKS_HALFPERIOD = _TICKS_PERIOD // 2

FULL_SCALE = 65535  # analogio.AnalogIn.value is always scaled to 16-bit


def ticks_diff(t1, t2):
    """Signed difference between two supervisor.ticks_ms() values, correctly
    handling the 2**29 ms rollover. CircuitPython has no built-in ticks_diff,
    and naive subtraction goes large-negative at the wrap."""
    diff = (t1 - t2) & _TICKS_MAX
    diff = ((diff + _TICKS_HALFPERIOD) & _TICKS_MAX) - _TICKS_HALFPERIOD
    return diff


class LoopState:
    def __init__(self):
        self.running = True
        self.mqtt = None
        self.last_publish = 0
        self.last_connect = 0
        self.reconnects = 0
        self.mqtt_lock = asyncio.Lock()
        self.msg_time = 0
        self.uptime_time = 0
        self.status_led = True


async def measure_uptime(state, disc):
    start_time = time.time()
    reset_reason = str(microcontroller.cpu.reset_reason).rsplit(".", 1)[-1]
    print(f"Last reset reason: {reset_reason}")
    while state.running:
        try:
            t = time.time()
            uptime = t - start_time
            mem = gc.mem_free()
            rssi = wifi.radio.ap_info.rssi if wifi.radio.ap_info else None
            uptime_str = str(timedelta(seconds=uptime))
            print(f"Publishing uptime {uptime_str}")
            if state.mqtt.on_connected.is_set():
                await mqtt_publish(state, disc.topic("uptime", "state"), uptime_str)
                await mqtt_publish(state, disc.topic("uptime_seconds", "state"), int(uptime))
                await mqtt_publish(state, disc.topic("memory", "state"), mem)
                await mqtt_publish(state, disc.topic("reconnects", "state"), state.mqtt.reconnects)
                if rssi is not None:
                    await mqtt_publish(state, disc.topic("rssi", "state"), rssi)
        except Exception as e:
            print(f"Failed to publish uptime: {repr(e)}")
        await asyncio.sleep(10)


async def status_blinker(state):
    while state.running:
        if state.status_led:
            await blink(Color.GREEN, 1, interval=0.1)
            await asyncio.sleep(2)
        else:
            pixel[0] = Color.BLACK
            await asyncio.sleep(1)


async def sample_adc(pin, state, schmitt, sub_window_ms, sample_ms, oversample, name):
    """Sample the analog optical pickup and feed the adaptive Schmitt. The
    Schmitt produces two flips per bright/dark vane cycle (both edges), so
    schmitt.flips matches the old digital both-edge pulse count and
    pulses_per_unit calibration carries over."""
    adc = analogio.AnalogIn(pin)
    sample_interval = sample_ms / 1000
    win_t = supervisor.ticks_ms()
    print(f"Entering sampler for {name}  (sub_window={sub_window_ms}ms  sample={sample_ms}ms  oversample={oversample})")
    while state.running:
        # Oversample to tame the ESP32-S3 ADC noise.
        total = 0
        for _ in range(oversample):
            total += adc.value
        schmitt.feed(total // oversample)

        if ticks_diff(supervisor.ticks_ms(), win_t) >= sub_window_ms:
            schmitt.close_window()
            win_t = supervisor.ticks_ms()

        await asyncio.sleep(sample_interval)
    print(f"Exiting sampler for {name}")


async def calculate_value(state, schmitt, disc, pulses_per_unit, interval, name):
    """Publish flow rate, totaliser and a signal-swing diagnostic on each
    interval. Flips never run backwards, so the delta is always >= 0; the
    swing diagnostic surfaces optical health (real flow ~6-8% of FS, parked
    <=2.5%)."""
    prev_flips = 0
    total_units = 0
    print(f"Entering value loop for {name}")
    while state.running:
        t0 = supervisor.ticks_ms()
        current_flips = schmitt.flips
        delta = current_flips - prev_flips
        prev_flips = current_flips

        units_per_min = (delta / interval) * 60 / pulses_per_unit
        total_units += delta / pulses_per_unit
        swing_pct = schmitt.swing * 100.0 / FULL_SCALE

        print(f"flips: +{delta} ({current_flips} total)  flow: {units_per_min:.2f} L/min  total: {total_units:.2f} L")
        print(f"swing: {swing_pct:.1f}%  gated: {schmitt.gated}  base: {schmitt.baseline}")

        pixel[0] = Color.BLUE
        try:
            if state.mqtt.on_connected.is_set():
                await mqtt_publish(state, disc.topic("flow_rate", "state"), units_per_min)
                await mqtt_publish(state, disc.topic("water_total", "state"), total_units)
                await mqtt_publish(state, disc.topic("swing", "state"), swing_pct)
                pixel[0] = Color.BLACK
            else:
                print("Unable to send values: mqtt not connected.")
        except Exception as e:
            print(f"Failed to send values for {name}: {repr(e)}.")
        pixel[0] = Color.BLACK

        state.msg_time = ticks_diff(supervisor.ticks_ms(), t0) / 1000
        microcontroller.watchdog.feed()
        await asyncio.sleep(interval)
    print(f"Exiting value loop for {name}")


def create_tasks(state, disc,
                 on_connection_ok,
                 on_disconnected,
                 on_connected):
    name = os.getenv("counter_name", "water")
    pulses_per_unit = os.getenv("pulses_per_unit", 81)
    interval = os.getenv("report_interval", 60)
    pin = eval(os.getenv("sensor_pin", "board.D1"))

    env_ms = int(os.getenv("env_ms", 4500))
    sub_window_ms = int(os.getenv("sub_window_ms", 250))
    sample_ms = int(os.getenv("sample_ms", 2))
    oversample = int(os.getenv("oversample", 4))
    min_swing_pct = float(os.getenv("min_swing_pct", 3.5))
    hyst_pct = float(os.getenv("hyst_pct", 1.5))

    env_windows = max(1, env_ms // sub_window_ms)
    min_swing = int(min_swing_pct / 100 * FULL_SCALE)
    hyst = int(hyst_pct / 100 * FULL_SCALE)
    schmitt = AdaptiveSchmitt(env_windows, min_swing, hyst, FULL_SCALE)

    print(f"Schmitt: env={env_ms}ms ({env_windows} windows)  min_swing={min_swing} ({min_swing_pct}%)  hyst={hyst} ({hyst_pct}%)")
    print(f"Calibration: pulses_per_unit={pulses_per_unit}  report_interval={interval}s")

    tasks = []
    tasks.append(asyncio.create_task(sample_adc(pin, state, schmitt, sub_window_ms,
                                                 sample_ms, oversample, name)))
    tasks.append(asyncio.create_task(calculate_value(state, schmitt, disc,
                                                      pulses_per_unit, interval, name)))
    tasks.append(asyncio.create_task(measure_uptime(state, disc)))
    tasks.append(asyncio.create_task(status_blinker(state)))
    tasks.append(asyncio.create_task(wifi_supervisor(state,
                                                     on_connection_ok,
                                                     on_disconnected,
                                                     on_connected)))
    return tasks


def output_mem():
    print("Memory Info - gc.mem_free()")
    print("---------------------------")
    print("{} Bytes\n".format(gc.mem_free()))

    flash = os.statvfs('/')
    flash_size = flash[0] * flash[2]
    flash_free = flash[0] * flash[3]
    print("Flash - os.statvfs('/')")
    print("---------------------------")
    print("Size: {} Bytes\nFree: {} Bytes\n".format(flash_size, flash_free))


async def main():
    output_mem()

    tinys3.set_pixel_power(True)

    device_name = os.getenv("device_name", "Water Meter")

    disc = HADiscovery(device_name, "CircuitPython Water Meter", "water")
    disc.add_component("flow_rate", "sensor", {
        "name": "Flow rate",
        "device_class": "volume_flow_rate",
        "unit_of_measurement": "L/min",
        "state_class": "measurement",
        "suggested_display_precision": 2,
    })
    disc.add_component("water_total", "sensor", {
        "name": "Water total",
        "device_class": "water",
        "unit_of_measurement": "L",
        "state_class": "total_increasing",
    })
    disc.add_component("swing", "sensor", {
        "name": "Signal swing",
        "entity_category": "diagnostic",
        "state_class": "measurement",
        "unit_of_measurement": "%",
        "suggested_display_precision": 1,
    })
    disc.add_component("uptime", "sensor", {
        "name": "Uptime",
        "entity_category": "diagnostic",
    })
    disc.add_component("uptime_seconds", "sensor", {
        "name": "Uptime seconds",
        "entity_category": "diagnostic",
        "device_class": "duration",
        "unit_of_measurement": "s",
        "state_class": "total_increasing",
    })
    disc.add_component("memory", "sensor", {
        "name": "Memory",
        "unit_of_measurement": "B",
        "entity_category": "diagnostic",
    })
    disc.add_component("reconnects", "sensor", {
        "name": "Reconnects",
        "entity_category": "diagnostic",
    })
    disc.add_component("rssi", "sensor", {
        "name": "WiFi RSSI",
        "device_class": "signal_strength",
        "unit_of_measurement": "dBm",
        "state_class": "measurement",
        "entity_category": "diagnostic",
    })
    disc.add_component("status_led", "switch", {
        "name": "Status LED",
        "command_topic": True,
        "entity_category": "config",
    })

    state = LoopState()

    def on_connect(client):
        print("Publishing discovery payload...")
        client.publish(disc.discovery_topic, disc.discovery_payload_json(), retain=True)
        for topic in disc.command_topics():
            print(f"Subscribing to {topic}...")
            state.mqtt.subscribe(topic)
        state.mqtt.publish(disc.topic("status_led", "state"), "ON" if state.status_led else "OFF")

    def on_message(client, topic, message):
        if topic == disc.topic("status_led", "set"):
            state.status_led = message == "ON"
            state.mqtt.publish(disc.topic("status_led", "state"), message)

    state.mqtt = Mqtt(on_connect_callback=on_connect, on_message_callback=on_message)

    async def on_connection_ok():
        if state.mqtt.running == False:
            state.mqtt.start_supervisor()

    async def on_disconnected():
        await state.mqtt.stop_supervisor()
        state.mqtt.disconnect()

    async def on_connected():
        state.mqtt.init()
        if not state.mqtt.running:
            state.mqtt.start_supervisor()

    microcontroller.watchdog.timeout = 120
    microcontroller.watchdog.mode = WatchDogMode.RESET
    print("Watchdog enabled with 120s timeout.")

    tasks = create_tasks(state, disc,
                         on_connection_ok,
                         on_disconnected,
                         on_connected)

    await asyncio.gather(*tasks)

while True:
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"Unhandled exception: {repr(e)}")
        print("Restarting...")
