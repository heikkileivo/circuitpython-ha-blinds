import unittest

from mirror import Plan, plan_mirror, plan_tree_mirror


def listing(*names):
    """Entries as the web workflow's GET /fs/ returns them for plain files."""
    return [{"name": name, "directory": False, "file_size": 100} for name in names]


class PlanMirrorTest(unittest.TestCase):
    def test_uploads_the_wanted_files_and_deletes_stale_root_py_files(self):
        wanted = ["code.py", "discovery.py", "blinds.py"]
        device = listing("code.py", "discovery.py", "secrets.py", "mqtt.py")

        plan = plan_mirror(wanted, device)

        self.assertEqual(plan.uploads, ["blinds.py", "code.py", "discovery.py"])
        self.assertEqual(plan.stale, ["mqtt.py", "secrets.py"])

    def test_never_deletes_non_py_files_or_directories(self):
        device = listing("code.py", "settings.toml", "boot_out.txt", "code.py.bak") + [
            {"name": "lib", "directory": True, "file_size": 0},
            {"name": "old.py", "directory": True, "file_size": 0},
        ]

        plan = plan_mirror(["code.py"], device)

        self.assertEqual(plan.stale, [])

    def test_keeps_a_device_file_that_differs_from_a_wanted_file_only_in_case(self):
        # The device's FAT filesystem is case-insensitive: uploading code.py
        # overwrites Code.py, so deleting Code.py would delete the upload.
        device = listing("Code.py", "Blinds.py")

        plan = plan_mirror(["code.py", "blinds.py"], device)

        self.assertEqual(plan.stale, [])


class PlanTreeMirrorTest(unittest.TestCase):
    def test_creates_the_directories_and_uploads_the_files_the_device_is_missing(self):
        wanted = {
            "lib": None,
            "lib/adafruit_ticks.mpy": b"ticks",
            "lib/asyncio": None,
            "lib/asyncio/core.mpy": b"core",
        }
        device = {"lib": None}

        plan = plan_tree_mirror(wanted, device)

        self.assertEqual(plan.mkdirs, ["lib/asyncio/"])
        self.assertEqual(plan.uploads, ["lib/adafruit_ticks.mpy", "lib/asyncio/core.mpy"])
        self.assertEqual(plan.replaced, [])
        self.assertEqual(plan.stale, [])

    def test_uploads_only_the_files_whose_contents_differ(self):
        wanted = {
            "lib": None,
            "lib/adafruit_minimqtt.mpy": b"minimqtt 8.1.0",
            "lib/adafruit_ticks.mpy": b"ticks",
        }
        device = {
            "lib": None,
            "lib/adafruit_minimqtt.mpy": b"minimqtt 7.10.0",
            "lib/adafruit_ticks.mpy": b"ticks",
        }

        plan = plan_tree_mirror(wanted, device)

        self.assertEqual(plan, Plan(replaced=[], mkdirs=[], uploads=["lib/adafruit_minimqtt.mpy"], stale=[]))

    def test_deletes_stale_files_and_a_stale_directory_along_with_its_contents(self):
        wanted = {"lib": None, "lib/adafruit_minimqtt.mpy": b"minimqtt"}
        device = {
            "lib": None,
            "lib/adafruit_minimqtt.mpy": b"minimqtt",
            "lib/adafruit_minimqtt": None,
            "lib/adafruit_minimqtt/__init__.mpy": b"init",
            "lib/adafruit_minimqtt/matcher": None,
            "lib/adafruit_minimqtt/matcher/matcher.mpy": b"matcher",
            "lib/code.py.bak": b"old",
        }

        plan = plan_tree_mirror(wanted, device)

        self.assertEqual(plan.stale, ["lib/adafruit_minimqtt/", "lib/code.py.bak"])
        self.assertEqual(plan.replaced, [])

    def test_matches_device_entries_that_differ_from_wanted_ones_only_in_case(self):
        # The device's FAT filesystem is case-insensitive, so Asyncio/ is the
        # same directory as asyncio/ and must not be deleted.
        wanted = {
            "lib": None,
            "lib/adafruit_ticks.mpy": b"ticks",
            "lib/asyncio": None,
            "lib/asyncio/core.mpy": b"core 2",
        }
        device = {
            "Lib": None,
            "Lib/Adafruit_Ticks.mpy": b"ticks",
            "Lib/Asyncio": None,
            "Lib/Asyncio/core.mpy": b"core 1",
        }

        plan = plan_tree_mirror(wanted, device)

        self.assertEqual(plan, Plan(replaced=[], mkdirs=[], uploads=["lib/asyncio/core.mpy"], stale=[]))

    def test_replaces_a_device_file_where_a_directory_is_wanted_and_the_other_way_round(self):
        wanted = {
            "lib": None,
            "lib/adafruit_ticks.mpy": b"ticks",
            "lib/asyncio": None,
            "lib/asyncio/core.mpy": b"core",
        }
        device = {
            "lib": None,
            "lib/adafruit_ticks.mpy": None,
            "lib/adafruit_ticks.mpy/leftover.mpy": b"leftover",
            "lib/asyncio": b"a file",
        }

        plan = plan_tree_mirror(wanted, device)

        self.assertEqual(plan.replaced, ["lib/adafruit_ticks.mpy/", "lib/asyncio"])
        self.assertEqual(plan.mkdirs, ["lib/asyncio/"])
        self.assertEqual(plan.uploads, ["lib/adafruit_ticks.mpy", "lib/asyncio/core.mpy"])
        self.assertEqual(plan.stale, [])


if __name__ == "__main__":
    unittest.main()
