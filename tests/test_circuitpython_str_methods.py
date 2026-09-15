"""str methods that CPython has but CircuitPython doesn't, called in any .py
file that's deployed to a device.

The Middle blind's stage 8 code failed every boot at servo.capitalize() in
components.py, with "AttributeError: 'str' object has no attribute
'capitalize'" (gate #56, #103). CPython has it, so every host test passed.

CircuitPython's str has only the methods in CIRCUITPYTHON_STR_METHODS, the
str locals table in its py/objstr.c at 9.1.1. center, partition, rpartition
and splitlines depend on build flags, so they count as present. Any call of
a method by one of the other names counts, whatever the object: the source
doesn't say which objects are strings.
"""

import io
import tokenize
import unittest

from tests.test_circuitpython_syntax import DEPLOYED, REPO

CIRCUITPYTHON_STR_METHODS = frozenset((
    "center", "count", "encode", "endswith", "find", "format", "index", "isalpha",
    "isdigit", "islower", "isspace", "isupper", "join", "lower", "lstrip",
    "partition", "replace", "rfind", "rindex", "rpartition", "rsplit", "rstrip",
    "split", "splitlines", "startswith", "strip", "upper"))
MISSING = frozenset(name for name in dir(str) if not name.startswith("_")) - CIRCUITPYTHON_STR_METHODS


def missing_method_calls(source):
    """The line and name of each call of a method named like a str method
    CircuitPython lacks, as in .capitalize()."""
    tokens = [token for token in tokenize.generate_tokens(io.StringIO(source).readline)
              if token.type not in (tokenize.NL, tokenize.COMMENT)]
    calls = []
    for dot, name, paren in zip(tokens, tokens[1:], tokens[2:]):
        if (dot.string == "." and name.type == tokenize.NAME and name.string in MISSING
                and paren.string == "("):
            calls.append((name.start[0], name.string))
    return calls


class MissingMethodCallsTest(unittest.TestCase):
    def test_a_call_of_a_method_circuitpython_lacks_is_found(self):
        source = 'servo = "lift"\nname = servo.capitalize() + " temperature"\n'

        self.assertEqual(missing_method_calls(source), [(2, "capitalize")])

    def test_calls_of_methods_circuitpython_has_are_accepted(self):
        source = 's = "a b".split()\nt = ",".join(s).upper().strip()\n'

        self.assertEqual(missing_method_calls(source), [])

    def test_the_names_used_otherwise_are_accepted(self):
        source = 'title = "x"\nd = {"title": title}\nprint(d["title"], title)\n'

        self.assertEqual(missing_method_calls(source), [])


class DeployedCodeTest(unittest.TestCase):
    def test_deployed_code_calls_no_str_method_circuitpython_lacks(self):
        self.assertTrue(DEPLOYED)
        for path in DEPLOYED:
            with self.subTest(path=str(path.relative_to(REPO))):
                self.assertEqual(missing_method_calls(path.read_text()), [])


if __name__ == "__main__":
    unittest.main()
