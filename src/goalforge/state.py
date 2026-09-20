from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid


def git(root: Path, *args: str) -> str:
    command = ["git", "-c", "core.hooksPath=" + os.devnull,
               "-c", "commit.gpgsign=false", "-c", "core.quotePath=false", "-C", str(root), *args]
    result = subprocess.run(command, text=True, encoding="utf-8", errors="replace",
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    if result.returncode:
        raise RuntimeError(f"Git {args[0]} failed: {result.stderr.strip()[:1200]}")
    return result.stdout.strip()


def save(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def fingerprint(proposal: dict) -> str:
    text = " ".join((proposal["title"] + " " + proposal["approach"]).lower().split())
    return hashlib.sha256(text.encode()).hexdigest()


class Store:
    def __init__(self, root: Path, secret: str = ""):
        self.root, self.secret = root, secret
        self.file = root / "state.json"

    def redact(self, value):
        text = json.dumps(value, ensure_ascii=False)
        if self.secret:
            # Escape the secret identically to JSON serialization.
            text = text.replace(json.dumps(self.secret, ensure_ascii=False)[1:-1], "[REDACTED]")
        return json.loads(text)

    def read(self) -> dict:
        return json.loads(self.file.read_text(encoding="utf-8"))

    def write(self, state: dict) -> None:
        save(self.file, self.redact(state))

    def event(self, kind: str, **values) -> None:
        record = self.redact({"time": time.time(), "kind": kind, **values})
        with (self.root / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    @contextmanager
    def lock(self):
        # Kernel locks are automatically released on crash; no stale PID guessing.
        handle = (self.root / "run.lock").open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                handle.write(b"0")
                handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise RuntimeError("This run is already active in another process.") from None
        try:
            yield
        finally:
            handle.close()


def create_run(repo: Path, runs: Path, goal: str, checks: list[list[str]],
               protected: list[str], directive: str, model: str, base_url: str) -> Store:
    repo = Path(git(repo.resolve(), "rev-parse", "--show-toplevel"))
    if git(repo, "status", "--porcelain"):
        raise ValueError("Commit or stash your existing changes first; the agent starts from committed HEAD.")
    commit = git(repo, "rev-parse", "HEAD")
    if runs.resolve().is_relative_to(repo.resolve()):
        raise ValueError("The run storage directory must be outside the target repository.")
    run_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
    root = runs.resolve() / run_id
    root.mkdir(parents=True)
    branch = "goalforge/" + run_id
    git(repo, "worktree", "add", "-b", branch, str(root / "workspace"), commit)
    store = Store(root)
    store.write({"id": run_id, "repo": str(repo), "workspace": str(root / "workspace"),
                 "branch": branch, "start_commit": commit, "accepted_commit": commit,
                 "goal": goal, "checks": checks, "protected": protected, "directive": directive,
                 "model": model, "base_url": base_url, "status": "ready", "attempt": 0,
                 "history": [], "baseline": None, "usage": {}})
    return store


def rollback(state: dict) -> None:
    workspace = Path(state["workspace"])
    # Only touch the dedicated worktree recorded at creation. Original checkout is untouched.
    if git(workspace, "symbolic-ref", "--short", "HEAD") != state["branch"]:
        raise RuntimeError("Worktree branch changed; refusing to reset another branch. Restore the run branch manually.")
    git(workspace, "reset", "--hard", state["accepted_commit"])
    git(workspace, "clean", "-fd")
