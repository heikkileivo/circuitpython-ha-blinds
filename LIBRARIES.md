# Device libraries

`lib/` holds the exact CircuitPython libraries the devices run. `python3 deploy.py --libs <device>` mirrors it to the device's `/lib/`: it uploads what's missing or differs, and deletes what isn't in `lib/`.

Every file is byte-identical to the `lib/` folder of its release asset below. These are the 9.x builds, which CircuitPython 9.1.x loads. The baseline was pulled from the Left blind (`upstairs-living-room-left-blinds`, CircuitPython 9.1.3) on 2026-09-12.

asyncio is **3.1.1**, ahead of the CircuitPython 10.3.1 upgrade (#128): 10.x removed `_asyncio.push_head`, `push_sorted` and `pop_head`, which asyncio 1.3.2 uses and 3.0.0 stopped using. 3.1.1 uses only `push`, `pop`, `peek` and `remove`, and 9.1.3's `task_queue_push_obj` already takes the optional key, so it runs on both. It ships ahead of the firmware so that one change is soaked on its own. 3.1.1 has no `manifest.mpy`, so `--libs` deletes the one already on each device.

The `.mpy` format is **6 for both CircuitPython 9.x and 10.x** (`py/persistentcode.h` at tags 9.1.3 and 10.3.1); only `MPY_SUB_VERSION` moved, and that is checked only for native-arch `.mpy` files. The `9.x-mpy` and `10.x-mpy` assets differ in the library sources they carry, not in loadability, so each row should still track the asset matching the firmware the devices run. When the fleet is on 10.3.1, every row moves to its `10.x-mpy` asset.

| Library | Version | Files in `lib/` | Source |
|---|---|---|---|
| asyncio | 3.1.1 | `asyncio/` | [adafruit-circuitpython-asyncio-9.x-mpy-3.1.1.zip](https://github.com/adafruit/Adafruit_CircuitPython_asyncio/releases/download/3.1.1/adafruit-circuitpython-asyncio-9.x-mpy-3.1.1.zip) |
| ConnectionManager | 3.1.1 | `adafruit_connection_manager.mpy` | [adafruit-circuitpython-connectionmanager-9.x-mpy-3.1.1.zip](https://github.com/adafruit/Adafruit_CircuitPython_ConnectionManager/releases/download/3.1.1/adafruit-circuitpython-connectionmanager-9.x-mpy-3.1.1.zip) |
| DateTime | 1.2.7 | `adafruit_datetime.mpy` | [adafruit-circuitpython-datetime-9.x-mpy-1.2.7.zip](https://github.com/adafruit/Adafruit_CircuitPython_datetime/releases/download/1.2.7/adafruit-circuitpython-datetime-9.x-mpy-1.2.7.zip) |
| Debouncer | 2.0.8 | `adafruit_debouncer.mpy` | [adafruit-circuitpython-debouncer-9.x-mpy-2.0.8.zip](https://github.com/adafruit/Adafruit_CircuitPython_Debouncer/releases/download/2.0.8/adafruit-circuitpython-debouncer-9.x-mpy-2.0.8.zip) |
| MiniMQTT | 8.1.0 | `adafruit_minimqtt/` | [adafruit-circuitpython-minimqtt-9.x-mpy-8.1.0.zip](https://github.com/adafruit/Adafruit_CircuitPython_MiniMQTT/releases/download/8.1.0/adafruit-circuitpython-minimqtt-9.x-mpy-8.1.0.zip) |
| ticks | 1.0.13 | `adafruit_ticks.mpy` | [adafruit-circuitpython-ticks-9.x-mpy-1.0.13.zip](https://github.com/adafruit/Adafruit_CircuitPython_Ticks/releases/download/1.0.13/adafruit-circuitpython-ticks-9.x-mpy-1.0.13.zip) |

## Changing a library

1. Download the new version's `9.x-mpy` release asset and replace the library's files in `lib/` with the ones in the asset's `lib/` folder.
2. Update its row here.
3. Run `python3 deploy.py --libs --dry-run <device>` against a device that runs the current `lib/`. It should list only that library's files.
