# Device libraries

`lib/` holds the exact CircuitPython libraries the devices run. `python3 deploy.py --libs <device>` mirrors it to the device's `/lib/`: it uploads what's missing or differs, and deletes what isn't in `lib/`.

Every file is byte-identical to the `lib/` folder of its release asset below. These are the 9.x builds (`.mpy` format 6), which CircuitPython 9.1.x loads. The baseline was pulled from the Left blind (`upstairs-living-room-left-blinds`, CircuitPython 9.1.3) on 2026-09-12.

| Library | Version | Files in `lib/` | Source |
|---|---|---|---|
| asyncio | 1.3.2 | `asyncio/` | [adafruit-circuitpython-asyncio-9.x-mpy-1.3.2.zip](https://github.com/adafruit/Adafruit_CircuitPython_asyncio/releases/download/1.3.2/adafruit-circuitpython-asyncio-9.x-mpy-1.3.2.zip) |
| ConnectionManager | 3.1.1 | `adafruit_connection_manager.mpy` | [adafruit-circuitpython-connectionmanager-9.x-mpy-3.1.1.zip](https://github.com/adafruit/Adafruit_CircuitPython_ConnectionManager/releases/download/3.1.1/adafruit-circuitpython-connectionmanager-9.x-mpy-3.1.1.zip) |
| DateTime | 1.2.7 | `adafruit_datetime.mpy` | [adafruit-circuitpython-datetime-9.x-mpy-1.2.7.zip](https://github.com/adafruit/Adafruit_CircuitPython_datetime/releases/download/1.2.7/adafruit-circuitpython-datetime-9.x-mpy-1.2.7.zip) |
| Debouncer | 2.0.8 | `adafruit_debouncer.mpy` | [adafruit-circuitpython-debouncer-9.x-mpy-2.0.8.zip](https://github.com/adafruit/Adafruit_CircuitPython_Debouncer/releases/download/2.0.8/adafruit-circuitpython-debouncer-9.x-mpy-2.0.8.zip) |
| MiniMQTT | 7.10.0 | `adafruit_minimqtt/` | [adafruit-circuitpython-minimqtt-9.x-mpy-7.10.0.zip](https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/releases/download/7.10.0/adafruit-circuitpython-minimqtt-9.x-mpy-7.10.0.zip) |
| ticks | 1.0.13 | `adafruit_ticks.mpy` | [adafruit-circuitpython-ticks-9.x-mpy-1.0.13.zip](https://github.com/adafruit/Adafruit_CircuitPython_Ticks/releases/download/1.0.13/adafruit-circuitpython-ticks-9.x-mpy-1.0.13.zip) |

## Changing a library

1. Download the new version's `9.x-mpy` release asset and replace the library's files in `lib/` with the ones in the asset's `lib/` folder.
2. Update its row here.
3. Run `python3 deploy.py --libs --dry-run <device>` against a device that runs the current `lib/`. It should list only that library's files.
