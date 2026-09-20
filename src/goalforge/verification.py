"""Shared verification command defaults for terminal and browser runs."""
import shlex
import sys


def verification_commands(commands):
    checks = [shlex.split(command) for command in commands if command.strip()]
    if any(not command or not command[0] for command in checks):
        raise ValueError("Verification commands must name an executable")
    return checks or [[sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]]
