"""Literal, single-line .env configuration without modifying the process environment."""
from __future__ import annotations

import os
from pathlib import Path
import re


def read_env(path: Path, required: bool = False) -> dict[str, str]:
    if not path.exists() and not required:
        return {}
    values = {}
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)", line)
        if not match:
            raise ValueError(f"Invalid .env assignment at {path}:{number}; expected NAME=value")
        name, value = match.groups()
        if value.startswith(("'", '"')):
            quoted = re.fullmatch(r"(['\"])(.*?)\1\s*(?:#.*)?", value)
            if not quoted:
                raise ValueError(f"Invalid .env quotes at {path}:{number}; use single-line values")
            value = quoted.group(2)
        else:
            value = "" if value.startswith("#") else re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        values[name] = value
    return values


def settings(env_file: Path | None = None) -> dict[str, str]:
    # Explicit environment variables take priority; .env is never sourced as shell code.
    return {**read_env(env_file or Path.cwd() / ".env", required=env_file is not None),
            **os.environ}
