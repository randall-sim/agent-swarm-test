"""Create a fresh demo repo, then run real parallel agents with your .env settings."""
from __future__ import annotations

import argparse
from pathlib import Path
import shlex
import shutil
import sys
import tempfile

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from goalforge.cli import main, positive, worker_count
from goalforge.state import git


GOAL = (
    "Implement the three independent utility functions: slugify in text_tools.py "
    "(lowercase ASCII letters/digits, replace runs of other characters with one hyphen, "
    "strip edge hyphens); median in stats_tools.py (odd/even numeric inputs, no mutation, "
    "ValueError for empty input); and is_leap_year in date_tools.py (Gregorian rule). "
    "Use three independent coding tasks, one per module, when at least three workers "
    "are available. Keep the existing acceptance tests unchanged."
)


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=PROJECT / ".env")
    parser.add_argument("--demo-dir", type=Path, help="New directory for the demo (must not exist)")
    parser.add_argument("--workers", type=worker_count, default=3)
    parser.add_argument("--max-calls", type=positive, default=80)
    parser.add_argument("--yes", action="store_true", help="Allow model commands without prompts")
    parser.add_argument("--prepare-only", action="store_true", help="Create the demo without calling an LLM")
    args = parser.parse_args(argv)
    if args.demo_dir:
        destination = args.demo_dir.resolve()
        destination.mkdir(parents=True, exist_ok=False)
    else:
        demos = PROJECT / "demos"
        demos.mkdir(exist_ok=True)
        destination = Path(tempfile.mkdtemp(prefix="goalforge-parallel-demo-", dir=demos))
    shutil.copytree(PROJECT / "examples" / "parallel_project", destination, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (destination / ".gitignore").write_text("__pycache__/\n*.pyc\n.env\n", encoding="utf-8")
    git(destination, "init")
    git(destination, "add", ".")
    git(destination, "-c", "user.name=GoalForge Demo", "-c", "user.email=demo@localhost",
        "commit", "-m", "Initial parallel demo with three independent tasks")
    print(f"Demo repository: {destination}", flush=True)
    if args.prepare_only:
        print("Prepared only; no LLM requests were made.")
        return 0
    return main(["run", GOAL, "--repo", str(destination), "--env-file", str(args.env_file.resolve()),
                 "--check", shlex.join([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]),
                 "--protect", "tests", "--workers", str(args.workers), "--iterations", "3",
                 "--max-calls", str(args.max_calls)] + (["--yes"] if args.yes else []))


if __name__ == "__main__":
    raise SystemExit(run())
