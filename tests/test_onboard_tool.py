"""The onboarding host tool (#113): reading the onboard module's marker
lines out of REPL output, and stopping at a failed step, without a device.

The REPL output is framed as CircuitPython 9.1's web-workflow serial sends
it: a title escape, paste mode's "=== " echo of the code, CRLF line ends,
and the prompt at the end. The marker lines in it come from the device's
onboard module itself, run on the fake servo bus, so the tool reads what the
module really prints.
"""

import asyncio
import importlib.util
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

import cover_state
import onboard as device_onboard
import persist
from onboard import Onboarding
from tests.test_onboard import factory_servo
from tests.test_servo_health import FakeBus, FakeServo

TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.append(str(TOOLS))
# Loaded by path: "onboard" is the device module on the tests' path.
_spec = importlib.util.spec_from_file_location("onboard_tool", TOOLS / "onboard.py")
tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool)

TITLE = "\x1b]0;\U0001f40d192.168.1.103 | REPL | 9.1.1\x1b\\"


def repl_output(code, printed):
    """What the REPL sends back for code run in paste mode that printed the
    lines printed."""
    echo = "\r\n=== ".join(code.split("\n"))
    return (TITLE + "\r\npaste mode; Ctrl-C to cancel, Ctrl-D to finish\r\n=== " + echo
            + "\r\n" + "".join(line + "\r\n" for line in printed) + ">>> ")


def device_run(step, *servos, args=(), store=None):
    """The lines the device's onboard module prints for one step on a fake
    bus: step is the Onboarding method, called with args."""
    lines = []
    onboarding = Onboarding(FakeBus(*servos), store, out=lines.append)
    getattr(onboarding, step)(*args)
    return lines


TRACEBACK_OUTPUT = (TITLE + "\r\npaste mode; Ctrl-C to cancel, Ctrl-D to finish\r\n"
                    "=== import onboard\r\n=== o = onboard.on_device()\r\n"
                    "Traceback (most recent call last):\r\n"
                    '  File "<stdin>", line 2, in <module>\r\n'
                    "ValueError: TX in use\r\n>>> ")


class MarkerTest(unittest.TestCase):
    def test_the_markers_are_read_from_the_output_and_the_echo_is_skipped(self):
        output = repl_output('o.onboard("lift")', device_run("onboard", factory_servo(),
                                                             args=("lift",)))

        steps = [marker["step"] for marker in tool.markers(output)]

        self.assertEqual(steps, ["scan", "identify", "lock_off", "angle_limits", "id",
                                 "baud_rate", "read_back", "lock_on"])

    def test_a_marker_keeps_its_values(self):
        output = repl_output("o.bus_check()", device_run(
            "bus_check", FakeServo(1), FakeServo(2, angle_limits=(10, 1000))))

        (marker,) = tool.markers(output)

        self.assertEqual(marker["angle_limits"], {"lift": [0, 0], "tilt": [10, 1000]})

    def test_the_tool_looks_for_the_device_modules_marker(self):
        self.assertEqual(tool.MARKER, device_onboard.MARKER)

    def test_a_marker_cut_off_by_a_timeout_is_skipped(self):
        output = repl_output("o.bus_check()", ['ONBOARD {"step": "bus_check", "ok": tr'])

        self.assertEqual(tool.markers(output[:-len("\r\n>>> ")]), [])

    def test_a_traceback_is_found(self):
        self.assertTrue(tool.has_traceback(TRACEBACK_OUTPUT))
        self.assertFalse(tool.has_traceback(repl_output("o.bus_check()", [])))


class OutcomeTest(unittest.TestCase):
    def test_a_step_passes_when_its_last_marker_is_there_and_every_marker_went_through(self):
        output = repl_output('o.onboard("tilt")', device_run("onboard", factory_servo(),
                                                             args=("tilt",)))

        self.assertEqual(tool.outcome(output, "lock_on"), (True, None))

    def test_a_refusal_fails_at_its_step(self):
        output = repl_output('o.onboard("tilt")', device_run(
            "onboard", factory_servo(), FakeServo(2), args=("tilt",)))

        self.assertEqual(tool.outcome(output, "lock_on"), (False, "the scan step failed"))

    def test_a_traceback_fails_the_step(self):
        self.assertEqual(tool.outcome(TRACEBACK_OUTPUT, "lock_on"),
                         (False, "the device raised an error"))

    def test_output_that_never_reached_the_prompt_timed_out(self):
        output = repl_output('o.onboard("tilt")', device_run(
            "onboard", factory_servo(), args=("tilt",)))[:-len(">>> ")]

        self.assertEqual(tool.outcome(output, "lock_on"), (False, "no reply in time"))

    def test_a_step_that_stopped_short_of_its_last_marker_fails(self):
        # For example, the onboard module on the device is an old one.
        output = repl_output('o.verify("tilt")', [])

        self.assertEqual(tool.outcome(output, "verify"), (False, "no verify marker"))


INTERRUPT_OUTPUT = (TITLE + "Traceback (most recent call last):\r\n"
                    '  File "code.py", line 505, in <module>\r\n'
                    "KeyboardInterrupt: \r\nCode done running.\r\n\r\n"
                    "Press any key to enter the REPL. Use CTRL-D to reload.\r\n\r\n"
                    "Adafruit CircuitPython 9.1.1 on 2024-07-22; TinyS3 with ESP32S3\r\n>>> ")


def recorded(role, **broken):
    """Each step's REPL output for a good onboarding into role, by the call
    that starts it, with the steps in broken replaced."""
    record = persist.encode(cover_state.DOWN, 3.5, 12.25)
    onboarded = factory_servo()
    outputs = {"onboard": device_run("onboard", onboarded, args=(role,))}
    onboarded.power_cycle()
    outputs["verify"] = device_run("verify", onboarded, args=(role,))
    outputs["forget_travel"] = device_run(
        "forget_travel", store=persist.Store(bytearray(record)), args=(role,))
    other = FakeServo(2, angle_limits=(10, 1000)) if role == "lift" else FakeServo(1)
    outputs["bus_check"] = device_run("bus_check", onboarded, other)
    outputs = {call: repl_output(f"o.{call}(...)", lines) for call, lines in outputs.items()}
    outputs.update(broken)
    return outputs


class FakeRepl:
    """A blind's REPL that answers each call with its recorded output."""

    def __init__(self, outputs, interrupt=INTERRUPT_OUTPUT):
        self.outputs = outputs
        self.interrupt_output = interrupt
        self.calls = []

    def interrupt(self, timeout=10):
        self.calls.append("interrupt")
        return self.interrupt_output

    def run(self, code, timeout=20, on_output=None):
        (call,) = [call for call in self.outputs if f"o.{call}(" in code]
        self.calls.append(call)
        # In two parts, as a websocket may deliver it.
        output = self.outputs[call]
        if on_output:
            on_output(output[:len(output) // 2])
            on_output(output[len(output) // 2:])
        return output

    def reload(self, timeout=20):
        self.calls.append("reload")
        return ""


class Operator:
    """The operator at the prompts: presses Enter, and keeps what the tool
    printed."""

    def __init__(self):
        self.asked = []
        self.told = []

    def confirm(self, prompt):
        self.asked.append(prompt)
        return ""

    def say(self, text=""):
        self.told.append(text)

    def text(self):
        return "\n".join(self.told)


def run(role, repl):
    operator = Operator()
    ok = tool.run("middle", role, repl, confirm=operator.confirm, say=operator.say)
    return ok, operator


class FlowTest(unittest.TestCase):
    def test_a_lift_onboarding_runs_every_step_then_soft_reboots_and_passes(self):
        repl = FakeRepl(recorded("lift"))

        ok, operator = run("lift", repl)

        self.assertTrue(ok)
        self.assertEqual(repl.calls, ["interrupt", "onboard", "verify", "forget_travel",
                                      "bus_check", "reload"])
        # Only the new servo, then its power cycle, then the other servo back.
        self.assertEqual(len(operator.asked), 3)
        self.assertIn("PASS", operator.told[-1])

    def test_a_tilt_onboarding_leaves_the_travel_alone(self):
        repl = FakeRepl(recorded("tilt"))

        ok, _ = run("tilt", repl)

        self.assertTrue(ok)
        self.assertEqual(repl.calls, ["interrupt", "onboard", "verify", "bus_check", "reload"])

    def test_it_prints_what_answered_and_the_registers(self):
        _, operator = run("lift", FakeRepl(recorded("lift")))

        self.assertIn("ID 1 at 1000000 baud", operator.text())
        self.assertIn("registers [3, 25, 1, 9, 15]", operator.text())

    def test_a_refusal_stops_the_tool_and_leaves_the_blind_at_the_repl(self):
        refused = repl_output('o.onboard("lift")', device_run(
            "onboard", factory_servo(), FakeServo(2), args=("lift",)))
        repl = FakeRepl(recorded("lift", onboard=refused))

        ok, operator = run("lift", repl)

        self.assertFalse(ok)
        self.assertEqual(repl.calls, ["interrupt", "onboard"])
        self.assertIn("FAIL: the scan step failed", operator.text())
        self.assertIn("left at the REPL", operator.text())
        self.assertIn("Run the tool again", operator.text())

    def test_each_marker_is_told_once_as_it_arrives(self):
        _, operator = run("tilt", FakeRepl(recorded("tilt")))

        told = [line.strip().split(":")[0] for line in operator.told if line.startswith("  ")]
        self.assertEqual(told, ["scan", "identify", "lock_off", "angle_limits", "id",
                                "baud_rate", "read_back", "lock_on", "verify", "bus_check"])

    def test_a_step_that_timed_out_may_still_be_running_so_it_says_to_wait(self):
        cut = recorded("lift")["onboard"][:-len(">>> ")]
        repl = FakeRepl(recorded("lift", onboard=cut))

        ok, operator = run("lift", repl)

        self.assertFalse(ok)
        self.assertIn("FAIL: no reply in time", operator.text())
        self.assertIn("may still be running", operator.text())
        self.assertNotIn("left at the REPL", operator.text())

    def test_a_setting_that_didnt_persist_stops_it_at_the_verify(self):
        volatile = factory_servo(volatile=(5,))
        Onboarding(FakeBus(volatile), None, out=lambda line: None).onboard("tilt")
        volatile.power_cycle()
        failed = repl_output('o.verify("tilt")', device_run("verify", volatile, args=("tilt",)))
        repl = FakeRepl(recorded("tilt", verify=failed))

        ok, operator = run("tilt", repl)

        self.assertFalse(ok)
        self.assertEqual(repl.calls, ["interrupt", "onboard", "verify"])
        self.assertIn("FAIL: the verify step failed", operator.text())

    def test_a_traceback_stops_it_and_shows_the_device_output(self):
        repl = FakeRepl(recorded("lift", bus_check=TRACEBACK_OUTPUT))

        ok, operator = run("lift", repl)

        self.assertFalse(ok)
        self.assertNotIn("reload", repl.calls)
        self.assertIn("ValueError: TX in use", operator.text())

    def test_a_blind_that_doesnt_reach_the_repl_stops_it_before_the_operator_unplugs_anything(self):
        repl = FakeRepl(recorded("lift"), interrupt=TITLE + "Code done running.\r\n")

        ok, operator = run("lift", repl)

        self.assertFalse(ok)
        self.assertEqual(repl.calls, ["interrupt"])
        self.assertEqual(operator.asked, [])
        self.assertIn("may still be running code.py", operator.text())
        self.assertNotIn("left at the REPL", operator.text())


class FakeWebsocket:
    """One serial websocket session: it keeps what it was sent, and sends
    back one recorded output."""

    def __init__(self, output):
        self.output = output
        self.sent = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, data):
        self.sent.append(data)

    async def recv(self):
        if self.output is None:
            # Nothing more comes: wait until the session's timeout.
            await asyncio.Event().wait()
        output, self.output = self.output, None
        return output


class TransportTest(unittest.TestCase):
    def test_the_tool_opens_nothing_on_the_blind_but_the_serial_websocket(self):
        outputs = recorded("lift")
        answers = [INTERRUPT_OUTPUT] + [outputs[call] for call in
                                        ("onboard", "verify", "forget_travel", "bus_check")]
        answers.append("soft reboot\r\n")
        urls = []

        def connect(url, additional_headers, open_timeout):
            urls.append(url)
            return FakeWebsocket(answers.pop(0))

        async def no_pause(seconds):
            pass

        websockets = types.SimpleNamespace(connect=connect)
        dev = {"name": "middle", "host": "192.168.1.103", "password": "pw"}
        with mock.patch.dict(sys.modules, websockets=websockets), \
                mock.patch("asyncio.sleep", no_pause):
            ok = tool.run("middle", "lift", tool.Repl(dev, echo=False),
                          confirm=lambda prompt: "", say=lambda text="": None,
                          reload_timeout=0.01)

        self.assertTrue(ok)
        self.assertEqual(urls, ["ws://192.168.1.103/cp/serial/"] * 6)

    def test_the_tool_imports_no_http_client(self):
        source = (TOOLS / "onboard.py").read_text()

        for module in ("requests", "urllib", "http.client", "socket"):
            with self.subTest(module=module):
                self.assertNotIn(f"import {module}", source)


class CliTest(unittest.TestCase):
    def cli(self, script, *args):
        return subprocess.run([sys.executable, str(TOOLS / script), *args],
                              capture_output=True, text=True)

    def test_help_documents_the_device_and_the_role(self):
        result = self.cli("onboard.py", "--help")

        self.assertEqual(result.returncode, 0)
        self.assertIn("devices.json", result.stdout)
        self.assertIn("{lift,tilt}", result.stdout)

    def test_the_repl_cli_documents_its_modes(self):
        result = self.cli("repl.py", "--help")

        self.assertEqual(result.returncode, 0)
        for mode in ("--interrupt", "--reload", "--put", "code"):
            self.assertIn(mode, result.stdout)

    def test_the_repl_cli_with_nothing_to_do_is_a_usage_error(self):
        result = self.cli("repl.py", "some-blind")

        self.assertEqual(result.returncode, 2)
        self.assertIn("usage:", result.stderr)


if __name__ == "__main__":
    unittest.main()
