"""Syntax that CPython accepts but CircuitPython's compiler rejects, checked
in every .py file that's deployed to a device: the code, and the .py sources
in lib/.

CircuitPython 9.1.1 on the Middle blind (stage 3 gate, #40) failed to import
servo_health.py with "SyntaxError: invalid syntax" at an f-string continued
by a second one on the next line. Compiling snippets on its REPL showed:

    f"a{x}, " f"b{y}"   rejected
    f"a{x}, " "b"       accepted
    "a, " f"b{y}"       accepted

MicroPython also documents that an f-string can't be concatenated with a
plain literal that contains braces. So an f-string may only be joined to
brace-free plain strings; anything else needs an explicit +.
"""

import io
import tokenize
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEPLOYED = sorted(list(REPO.glob("shared/*.py")) + list(REPO.glob("devices/*/*.py"))
                  + list(REPO.glob("lib/**/*.py")))

# Tokens that can sit between two literals Python joins: line breaks inside
# brackets, and comments.
_GAP_TOKENS = (tokenize.NL, tokenize.COMMENT)


def _joined_runs(source):
    """Each run of two or more adjacent string literals, which Python joins
    into one, as a list of (line, text). An f-string's text is just its
    opening, prefix and quotes."""
    runs, run = [], []
    fstring_depth = 0
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        # An f-string comes as FSTRING_START ... FSTRING_END, with the tokens
        # of its fields, nested f-strings included, in between.
        if token.type == tokenize.FSTRING_START:
            if not fstring_depth:
                run.append((token.start[0], token.string))
            fstring_depth += 1
        elif fstring_depth:
            if token.type == tokenize.FSTRING_END:
                fstring_depth -= 1
        elif token.type == tokenize.STRING:
            run.append((token.start[0], token.string))
        elif token.type not in _GAP_TOKENS:
            # Anything else ends the run.
            if len(run) > 1:
                runs.append(run)
            run = []
    return runs


def _is_fstring(text):
    prefix = text[:len(text) - len(text.lstrip("rRbBfFuU"))]
    return "f" in prefix.lower()


def rejected_concatenations(source):
    """The first line of each joined run of literals that CircuitPython
    rejects: one with an f-string and another literal that isn't a
    brace-free plain string."""
    rejected = []
    for run in _joined_runs(source):
        fstrings = [text for _, text in run if _is_fstring(text)]
        braced_plain = [text for _, text in run
                        if not _is_fstring(text) and ("{" in text or "}" in text)]
        if fstrings and (len(fstrings) > 1 or braced_plain):
            rejected.append(run[0][0])
    return rejected


class RejectedConcatenationsTest(unittest.TestCase):
    def test_two_joined_fstrings_are_rejected(self):
        source = 'x = 1\ny = 2\ns = (f"a{x}, "\n     f"b{y}")\n'

        self.assertEqual(rejected_concatenations(source), [3])

    def test_an_fstring_joined_to_a_plain_string_with_braces_is_rejected(self):
        source = 'x = 1\ns = f"{x}" "a{}b"\n'

        self.assertEqual(rejected_concatenations(source), [2])

    def test_an_fstring_joined_to_brace_free_plain_strings_is_accepted(self):
        source = 'x = 1\ns = (f"a{x}, "\n     "b")\nt = ("a, "  # why\n     f"b{x}")\n'

        self.assertEqual(rejected_concatenations(source), [])

    def test_fstrings_joined_with_a_plus_are_accepted(self):
        source = 'x = 1\ns = (f"a{x}, "\n     + f"b{x}")\n'

        self.assertEqual(rejected_concatenations(source), [])

    def test_separate_fstrings_are_accepted(self):
        source = 'x = 1\nprint(f"{x}", f"{x:#04x}")\ns = [f"{x}",\n     f"{x}"]\n'

        self.assertEqual(rejected_concatenations(source), [])

    def test_a_nested_fstring_is_part_of_the_outer_one(self):
        source = 'x = 1\ns = (f"a{f\'{x}\'}"\n     "b")\nt = (f"a{f\'{x}\'}"\n     f"b{x}")\n'

        self.assertEqual(rejected_concatenations(source), [4])


class DeployedCodeTest(unittest.TestCase):
    def test_deployed_code_has_no_concatenation_circuitpython_rejects(self):
        self.assertTrue(DEPLOYED)
        for path in DEPLOYED:
            with self.subTest(path=str(path.relative_to(REPO))):
                self.assertEqual(rejected_concatenations(path.read_text()), [])


if __name__ == "__main__":
    unittest.main()
