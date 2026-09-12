"""Host tests, run from the repo root with `python3 -m unittest`.

Device code is deployed flat to the device root, so modules import each
other by bare name. Put shared/ and devices/blinds/ on the path to import them
the same way. devices/blinds/ goes last, so its code.py can't shadow the
standard library's code module.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared"))
sys.path.append(str(ROOT / "devices" / "blinds"))
