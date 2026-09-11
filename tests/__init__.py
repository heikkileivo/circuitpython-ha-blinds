"""Host tests, run from the repo root with `python3 -m unittest`.

Device code is deployed flat to the device root, so modules import each
other by bare name. Put shared/ on the path to import them the same way.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))
