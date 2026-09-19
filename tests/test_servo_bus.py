"""The servo bus values' one home (#111): servo_bus.py holds the lift and
tilt IDs and the bus baud rate, and every servo and every UART on the bus
uses them. Onboarding a replacement servo (#99) writes these same values.

The checks read the blinds' device code as source, so they cover boot.py,
safemode.py and code.py, which only run on the device.
"""

import ast
import unittest
from pathlib import Path

import servo_bus

REPO = Path(__file__).resolve().parents[1]
BLINDS = REPO / "devices" / "blinds"
DEVICE_CODE = sorted(BLINDS.glob("*.py"))

# The modules CircuitPython 9.1 builds in, as boot.py and safemode.py and
# the modules they import use them. Everything else must be deployed.
BUILT_IN = {"board", "busio", "microcontroller", "supervisor", "time"}
# The deployed modules' sources, by name. The deploy copies them flat to the
# device root.
DEPLOYED_MODULES = {path.stem: path for path in
                    list((REPO / "shared").glob("*.py")) + DEVICE_CODE}


def calls(path, name):
    """Every call in path's source to a function called name, bare or as an
    attribute, such as Servo(...) or busio.UART(...)."""
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Call):
            func = node.func
            if getattr(func, "id", None) == name or getattr(func, "attr", None) == name:
                yield node


def imports(path):
    """The top-level name of every module path's source imports, at any
    depth, so the imports inside functions too."""
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            yield from (alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            yield node.module.split(".")[0]


def imported_modules(path):
    """Every module path's source imports, and every module those import in
    turn, as far as the deployed modules go."""
    found, to_read = set(), [path]
    while to_read:
        for module in imports(to_read.pop()):
            if module not in found:
                found.add(module)
                if module in DEPLOYED_MODULES:
                    to_read.append(DEPLOYED_MODULES[module])
    return found


class ServoBusTest(unittest.TestCase):
    def test_the_values_are_what_the_blinds_servos_are_set_to(self):
        self.assertEqual((servo_bus.LIFT_ID, servo_bus.TILT_ID, servo_bus.BAUD_RATE),
                         (1, 2, 250000))

    def test_the_baud_rate_is_written_only_in_servo_bus(self):
        # And in onboard.py's table of Feetech's baud rates, where 250000 is
        # code 2 whatever rate the bus runs at.
        self.assertEqual(sum("250000" in line for line in
                             (BLINDS / "onboard.py").read_text().splitlines()), 1)
        for path in DEVICE_CODE:
            if path.name not in ("servo_bus.py", "onboard.py"):
                with self.subTest(path=path.name):
                    source = path.read_text()
                    self.assertNotIn("250000", source)
                    self.assertNotIn("250_000", source)

    def test_every_servo_takes_its_id_from_servo_bus(self):
        ids = []
        for path in DEVICE_CODE:
            for call in calls(path, "Servo"):
                with self.subTest(path=path.name, line=call.lineno):
                    ids.append(ast.unparse(call.args[0]))
                    self.assertIn(ids[-1], ("servo_bus.LIFT_ID", "servo_bus.TILT_ID"))
        # Blinds' lift and tilt, at least.
        self.assertCountEqual(set(ids), ("servo_bus.LIFT_ID", "servo_bus.TILT_ID"))

    def test_every_uart_takes_the_baud_rate_from_servo_bus(self):
        opened_in = set()
        for path in DEVICE_CODE:
            for call in calls(path, "UART"):
                opened_in.add(path.name)
                with self.subTest(path=path.name, line=call.lineno):
                    baud_rate = {kw.arg: kw.value for kw in call.keywords}["baudrate"]
                    self.assertEqual(ast.unparse(baud_rate), "servo_bus.BAUD_RATE")
        self.assertLessEqual({"code.py", "boot.py", "safemode.py"}, opened_in)

    def test_boot_and_safemode_import_only_modules_on_the_device(self):
        # They run before or instead of code.py, so a missing module would
        # leave the lift unstopped.
        for name in ("boot.py", "safemode.py"):
            for module in imported_modules(BLINDS / name):
                with self.subTest(file=name, module=module):
                    self.assertIn(module, BUILT_IN | set(DEPLOYED_MODULES))

    def test_nothing_imports_onboard_at_boot(self):
        # The host tool imports it from the REPL once code.py has stopped.
        for name in ("boot.py", "code.py", "safemode.py"):
            with self.subTest(file=name):
                self.assertNotIn("onboard", imported_modules(BLINDS / name))

    def test_servo_bus_imports_nothing(self):
        # boot.py and safemode.py import it, and stay light.
        self.assertEqual(list(imports(BLINDS / "servo_bus.py")), [])


if __name__ == "__main__":
    unittest.main()
