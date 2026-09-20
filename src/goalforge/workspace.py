"""File tools and bounded processes. A worktree is isolation, not a sandbox."""
from __future__ import annotations

from contextlib import nullcontext

import fnmatch
import os
from pathlib import Path
import signal
import subprocess
import tempfile
from typing import Callable


OUTPUT_LIMIT = 16000
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build"}


def sensitive(path: Path) -> bool:
    parts = [part.lower() for part in path.parts]
    return any(part == ".git" or part == ".env" or part.startswith(".env.")
               or part.endswith((".pem", ".key", ".p12"))
               or part in {"credentials.json", "id_rsa", "id_ed25519"}
               for part in parts)


def run_process(argv: list[str], cwd: Path, timeout: float, secret: str = "", *, fresh_python_cache: bool = False) -> dict:
    if not isinstance(argv, list) or not argv or any(not isinstance(x, str) or not x for x in argv):
        raise ValueError("argv must be a nonempty list of nonempty strings")
    env = {k: v for k, v in os.environ.items()
           if not any(word in k.upper() for word in ("API_KEY", "TOKEN", "SECRET", "PASSWORD"))
           and (not secret or secret not in v)}
    # A temporary disk file prevents a noisy process from exhausting Python memory.
    with (tempfile.TemporaryDirectory(prefix="goalforge-check-cache-") if fresh_python_cache else nullcontext(None)) as cache, tempfile.TemporaryFile() as output:
        if cache:
            # Same-size edits within one filesystem timestamp tick must not run stale .pyc files.
            env["PYTHONPYCACHEPREFIX"] = cache
            env["PYTHONDONTWRITEBYTECODE"] = "1"
        try:
            process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                       stdout=output, stderr=subprocess.STDOUT,
                                       start_new_session=(os.name != "nt"))
        except OSError as exc:
            return {"argv": argv, "returncode": 127, "output": str(exc), "timed_out": False}
        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               capture_output=True, check=False)
                if process.poll() is None:
                    process.kill()
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait()
        size = output.tell()
        output.seek(max(0, size - OUTPUT_LIMIT))
        result = output.read(OUTPUT_LIMIT).decode("utf-8", errors="replace")
    if secret:
        result = result.replace(secret, "[REDACTED]")
    return {"argv": argv, "returncode": process.returncode, "output": result,
            "timed_out": timed_out, "truncated": size > OUTPUT_LIMIT}


class Workspace:
    def __init__(self, root: Path, approve: Callable[[list[str]], bool],
                 timeout: float = 120, protected: list[str] | None = None, secret: str = "",
                 allowed_paths: set[str] | None = None):
        self.root = root.resolve()
        self.approve, self.timeout, self.secret = approve, timeout, secret
        self.protected = protected or []
        self.allowed_paths = allowed_paths

    def path(self, value: str, writing: bool = False) -> Path:
        if not isinstance(value, str) or not value or Path(value).is_absolute():
            raise ValueError("Use a relative workspace path")
        relative = Path(value)
        target = (self.root / relative).resolve()
        if not target.is_relative_to(self.root) or sensitive(relative) or sensitive(target.relative_to(self.root)):
            raise ValueError("Path is outside the workspace or is private")
        # Disallow symlink traversal even when the current target happens to be inside.
        current = self.root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("Symlink access is not allowed")
        if writing and self.is_protected(target.relative_to(self.root).as_posix()):
            raise ValueError("This path is protected by the user")
        if writing and self.allowed_paths is not None and target.relative_to(self.root).as_posix() not in self.allowed_paths:
            raise ValueError("This file belongs to another task; edit only your assigned files")
        return target

    def is_protected(self, relative: str) -> bool:
        return any(relative == p or relative.startswith(p.rstrip("/") + "/")
                   or fnmatch.fnmatchcase(relative, p) for p in self.protected)

    def files(self) -> list[str]:
        found = []
        for directory, dirs, files in os.walk(self.root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and
                             not (Path(directory) / d).is_symlink() and
                             not sensitive(Path(d)))
            for filename in sorted(files):
                path = Path(directory) / filename
                rel = path.relative_to(self.root)
                if not path.is_symlink() and not sensitive(rel):
                    found.append(rel.as_posix())
                    if len(found) >= 5000:
                        return found
        return found

    def execute(self, name: str, args: dict, writable: bool) -> dict:
        if not isinstance(args, dict):
            raise ValueError("Tool arguments must be an object")
        if name == "list_files":
            return {"files": self.files(), "limit": 5000}
        if name == "read_file":
            path = self.path(args["path"])
            start = int(args.get("start", 1))
            count = int(args.get("count", 200))
            if start < 1 or not 1 <= count <= 500:
                raise ValueError("start >= 1 and count between 1 and 500 required")
            if path.stat().st_size > 1_000_000:
                raise ValueError("File exceeds the 1 MB reading limit")
            lines = path.read_text(encoding="utf-8").splitlines()
            return {"text": "\n".join(f"{i + start}: {line}" for i, line in
                                      enumerate(lines[start - 1:start - 1 + count])), "lines": len(lines)}
        if name == "search":
            query = args["query"]
            if not isinstance(query, str) or not query:
                raise ValueError("query must be a nonempty literal string")
            hits = []
            for filename in self.files():
                path = self.path(filename)
                if path.stat().st_size > 1_000_000:
                    continue
                try:
                    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                        if query.lower() in line.lower():
                            hits.append({"path": filename, "line": number, "text": line[:500]})
                            if len(hits) == 60:
                                return {"hits": hits, "truncated": True}
                except UnicodeError:
                    continue
            return {"hits": hits, "truncated": False}
        if not writable:
            raise ValueError("This role only has read tools")
        if name == "run":
            argv = args["argv"]
            if not isinstance(argv, list) or not argv or any(not isinstance(x, str) for x in argv):
                raise ValueError("argv must be a list of strings")
            if not self.approve(argv):
                return {"denied": True, "message": "The user denied this command. Choose another approach."}
            return run_process(argv, self.root, self.timeout, self.secret)
        if name not in {"write_file", "replace_text", "delete_file"}:
            raise ValueError(f"Unknown tool: {name}")
        path = self.path(args["path"], writing=True)
        if name == "delete_file":
            path.unlink()
        elif name == "write_file":
            content = args["content"]
            if not isinstance(content, str) or len(content.encode()) > 200_000:
                raise ValueError("content must be a string under 200 KB")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        else:
            old, new = args["old"], args["new"]
            if not isinstance(old, str) or not old or not isinstance(new, str):
                raise ValueError("old must be nonempty and new must be a string")
            if path.stat().st_size > 1_000_000:
                raise ValueError("File exceeds the 1 MB editing limit")
            text = path.read_text(encoding="utf-8")
            if text.count(old) != 1:
                raise ValueError("old must match exactly once; include more surrounding text")
            path.write_text(text.replace(old, new, 1), encoding="utf-8")
        return {"ok": True, "path": args["path"]}
