import unittest

from mirror import plan_mirror


def listing(*names):
    """Entries as the web workflow's GET /fs/ returns them for plain files."""
    return [{"name": name, "directory": False, "file_size": 100} for name in names]


class PlanMirrorTest(unittest.TestCase):
    def test_uploads_the_wanted_files_and_deletes_stale_root_py_files(self):
        wanted = ["code.py", "discovery.py", "blinds.py"]
        device = listing("code.py", "discovery.py", "secrets.py", "mqtt.py")

        uploads, deletions = plan_mirror(wanted, device)

        self.assertEqual(uploads, ["blinds.py", "code.py", "discovery.py"])
        self.assertEqual(deletions, ["mqtt.py", "secrets.py"])

    def test_never_deletes_non_py_files_or_directories(self):
        device = listing("code.py", "settings.toml", "boot_out.txt", "code.py.bak") + [
            {"name": "lib", "directory": True, "file_size": 0},
            {"name": "old.py", "directory": True, "file_size": 0},
        ]

        uploads, deletions = plan_mirror(["code.py"], device)

        self.assertEqual(deletions, [])

    def test_keeps_a_device_file_that_differs_from_a_wanted_file_only_in_case(self):
        # The device's FAT filesystem is case-insensitive: uploading code.py
        # overwrites Code.py, so deleting Code.py would delete the upload.
        device = listing("Code.py", "Blinds.py")

        uploads, deletions = plan_mirror(["code.py", "blinds.py"], device)

        self.assertEqual(deletions, [])


if __name__ == "__main__":
    unittest.main()
