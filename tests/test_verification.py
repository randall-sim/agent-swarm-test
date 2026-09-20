import sys
import unittest

from goalforge.cli import parser
from goalforge.verification import verification_commands


class VerificationTests(unittest.TestCase):
    def test_absent_or_whitespace_uses_current_python(self):
        for commands in ([], [""], ["  ", "\t"]):
            self.assertEqual(verification_commands(commands),
                             [[sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]])
        self.assertEqual(parser().parse_args(["run", "Implement feature"]).check, [])

    def test_custom_checks_replace_default_and_preserve_quoting(self):
        self.assertEqual(verification_commands(['python "test script.py"', '', 'npm test']),
                         [['python', 'test script.py'], ['npm', 'test']])

    def test_invalid_command_does_not_silently_fall_back(self):
        for command in ('""', '"unterminated'):
            with self.assertRaises(ValueError):
                verification_commands([command])
