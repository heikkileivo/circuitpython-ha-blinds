"""The restart loop in code.py must reset the event loop between runs of
main(), and must do it before the next asyncio.run().

asyncio 1.3.2's run() was `return run_until_complete(create_task(coro))`. From
3.0.0 on it is:

    if cur_task is None:
        return run_until_complete(create_task(coro))
    else:
        raise RuntimeError("asyncio.run() cannot be called from a running event loop")

When main() itself raises, run_until_complete sets `cur_task = None` before
re-raising, so the common failure clears it. A failure that escapes that
handler -- raised by the scheduler outside its try, or a BaseException that
isn't an Exception -- leaves cur_task set, and then every later asyncio.run()
raises RuntimeError instead of running: one recoverable failure would become a
permanent one, on the path that recovers a blind unattended.

asyncio.new_event_loop() resets cur_task along with the task queue, so the
call code.py already makes covers this. This test keeps it there, since
nothing about the line says so and removing it looks harmless.
"""

import ast
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CODE = REPO / "devices" / "blinds" / "code.py"


def _calls(tree, attr):
    """The line of every asyncio.<attr>() call under tree."""
    return [node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute) and node.func.attr == attr
            and isinstance(node.func.value, ast.Name) and node.func.value.id == "asyncio"]


def restart_loop_calls(source):
    """(run line, new_event_loop line) for the module-level `while True:` loop
    that calls asyncio.run(). Either is None when the loop doesn't call it, and
    the pair is None when there's no such loop."""
    for node in ast.parse(source).body:
        if not (isinstance(node, ast.While)
                and isinstance(node.test, ast.Constant) and node.test.value is True):
            continue
        runs = _calls(node, "run")
        if not runs:
            continue
        resets = _calls(node, "new_event_loop")
        return runs[0], (resets[0] if resets else None)
    return None


class RestartLoopCallsTest(unittest.TestCase):
    def test_a_loop_that_resets_after_running_is_found(self):
        source = ("while True:\n"
                  "    asyncio.run(main())\n"
                  "    asyncio.new_event_loop()\n")

        self.assertEqual(restart_loop_calls(source), (2, 3))

    def test_a_loop_that_never_resets_reports_no_reset(self):
        source = "while True:\n    asyncio.run(main())\n"

        self.assertEqual(restart_loop_calls(source), (2, None))

    def test_a_reset_before_the_run_is_reported_in_order(self):
        source = ("while True:\n"
                  "    asyncio.new_event_loop()\n"
                  "    asyncio.run(main())\n")

        self.assertEqual(restart_loop_calls(source), (3, 2))

    def test_a_loop_that_doesnt_run_main_is_skipped(self):
        source = ("while True:\n"
                  "    feed()\n"
                  "while True:\n"
                  "    asyncio.run(main())\n"
                  "    asyncio.new_event_loop()\n")

        self.assertEqual(restart_loop_calls(source), (4, 5))

    def test_no_restart_loop_at_all(self):
        source = "asyncio.run(main())\n"

        self.assertIsNone(restart_loop_calls(source))


class DeployedCodeTest(unittest.TestCase):
    def test_the_restart_loop_resets_the_event_loop_after_each_run(self):
        calls = restart_loop_calls(CODE.read_text())

        self.assertIsNotNone(calls, "code.py has no `while True:` loop calling asyncio.run()")
        run_line, reset_line = calls
        self.assertIsNotNone(
            reset_line,
            "the restart loop must call asyncio.new_event_loop(): without it, a failure "
            "that escapes asyncio's own handler leaves cur_task set and every later "
            "asyncio.run() raises RuntimeError")
        self.assertGreater(
            reset_line, run_line,
            "asyncio.new_event_loop() must come after asyncio.run(), so the next run "
            "starts with cur_task cleared")


if __name__ == "__main__":
    unittest.main()
