"""Syntax that CPython accepts but CircuitPython's compiler rejects, checked
in every file that's deployed to a device.

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
DEPLOYED = sorted(list(REPO.glob("shared/*.py")) + list(REPO.glob("devices/*/*.py")))

# Tokens that can sit between two literals that are joined: line breaks
# inside brackets, and comments.
_BETWEEN = (tokenize.NL, tokenize.COMMENT)
# Python 3.12 splits an f-string into FSTRING_START ... FSTRING_END; older
# versions give one STRING token.
_FSTRING_START = getattr(tokenize, "FSTRING_START", None)
_FSTRING_END = getattr(tokenize, "FSTRING_END", None)


def _literals(source):
    """The source's string literals as (line, text, whether the next token
    after it is another literal it's joined to)."""
    lines = source.splitlines(keepends=True)

    def text(start, end):
        (row, col), (end_row, end_col) = start, end
        if row == end_row:
            return lines[row - 1][col:end_col]
        return lines[row - 1][col:] + "".join(lines[row:end_row - 1]) + lines[end_row - 1][:end_col]

    literals = []
    depth = 0
    start = None
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == _FSTRING_START:
            if depth == 0:
                start = token.start
            depth += 1
            continue
        if depth:
            if token.type == _FSTRING_END:
                depth -= 1
                if depth == 0:
                    literals.append((start[0], text(start, token.end), token.end))
            continue
        if token.type == tokenize.STRING:
            literals.append((token.start[0], text(token.start, token.end), token.end))
        elif token.type not in _BETWEEN and literals and literals[-1] is not None:
            # Anything else ends a run of joined literals.
            literals.append(None)
    return literals


def _runs(source):
    """Each run of adjacent string literals that Python joins, as a list of
    (line, text), from runs of two or more."""
    runs, run = [], []
    for literal in _literals(source) + [None]:
        if literal is None:
            if len(run) > 1:
                runs.append(run)
            run = []
        else:
            run.append(literal[:2])
    return runs


def _is_fstring(text):
    prefix = text[:len(text) - len(text.lstrip("rRbBfFuU"))]
    return "f" in prefix.lower()


def rejected_concatenations(source):
    """The first line of each joined run of literals that CircuitPython
    rejects: one with an f-string and another literal that isn't a
    brace-free plain string."""
    rejected = []
    for run in _runs(source):
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


class DeployedCodeTest(unittest.TestCase):
    def test_deployed_code_has_no_concatenation_circuitpython_rejects(self):
        self.assertTrue(DEPLOYED)
        for path in DEPLOYED:
            with self.subTest(path=str(path.relative_to(REPO))):
                self.assertEqual(rejected_concatenations(path.read_text()), [])


if __name__ == "__main__":
    unittest.main()
