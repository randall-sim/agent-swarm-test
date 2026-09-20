"""Guided, offline demo preparation. Never starts agents or makes API requests."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shlex
import shutil
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))
from goalforge.state import git
from run_parallel_demo import GOAL as UTILITY_GOAL

DEMOS = {
    "utilities": {
        "title": "Independent utilities · beginner",
        "description": "Three independent functions. A quick introduction to parallel worker dispatch.",
        "template": "parallel_project", "goal": UTILITY_GOAL, "calls": 80, "iterations": 3,
    },
    "taskboard": {
        "title": "Task board · shared contracts",
        "description": "Server-rendered frontend, request handler and SQLite adapter. Exercise cross-layer contracts, validation, persistence and HTML escaping.",
        "template": "taskboard_project", "calls": 160, "iterations": 5,
        "goal": "Implement the task board described in CONTRACT.md. The planner must first define shared storage/API/view contracts in its approach, then delegate independent modules to up to three coding workers. Record the chosen internal interfaces in CONTRACT.md. Reviewer: inspect agreement between layers, validation, HTML escaping and persistence; record actionable lessons for any remaining work so the next loop can repair it. Keep tests unchanged. Add further tests outside tests/ where useful. Complete only when the full contract works.",
    },
    "reservations": {
        "title": "Inventory reservations · advanced review loop",
        "description": "Frontend, API and transactional SQLite adapter. Test durable idempotency, concurrent reservations, rollback and contract consistency.",
        "template": "reservations_project", "calls": 220, "iterations": 6,
        "goal": "Implement the inventory reservation system described in CONTRACT.md. Before dispatch the planner must define shared adapter signatures, error semantics, transaction boundaries and API/view shapes in its approach. Use up to three independent coding workers and document the internal contract in CONTRACT.md. Reviewer: inspect cross-layer agreement, durable idempotency, concurrent writes and failed-operation rollback, not just passing tests. Feed concrete repair lessons into the next loop when work is incomplete. Keep tests unchanged. Add further tests outside tests/ where useful. Complete only when the whole contract is implemented.",
    },
}


def valid_name(value):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value):
        raise ValueError("Use 1–64 letters, numbers, hyphens or underscores; start with a letter or number.")
    return value


def prepare(kind, name, root=None, workers=3):
    if kind not in DEMOS:
        raise ValueError("Unknown demo")
    valid_name(name)
    if not 1 <= workers <= 8:
        raise ValueError("Workers must be between 1 and 8")
    spec = DEMOS[kind]
    root = Path(root) if root is not None else PROJECT / "demos"
    root.mkdir(parents=True, exist_ok=True)
    destination = root.resolve() / name
    destination.mkdir(exist_ok=False)
    try:
        shutil.copytree(PROJECT / "examples" / spec["template"], destination,
                        dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (destination / ".gitignore").write_text("__pycache__/\n*.pyc\n.env\n*.sqlite*\n", encoding="utf-8")
        check = shlex.join([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"])
        config = dict(demo=kind, goal=spec["goal"], workers=workers, max_calls=spec["calls"],
                      iterations=spec["iterations"], check=check, protected=["tests", "DEMO.md", "demo.json"])
        (destination / "demo.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        command = shlex.join([sys.executable, "-m", "goalforge", "run", spec["goal"],
                              "--repo", str(destination), "--env-file", str(PROJECT / ".env"),
                              "--workers", str(workers), "--iterations", str(spec["iterations"]),
                              "--max-calls", str(spec["calls"]), "--check", check,
                              "--protect", "tests", "--protect", "DEMO.md", "--protect", "demo.json"])
        guide = (f'# {spec["title"]}\n\n{spec["description"]}\n\n'
                 '## Run from the web UI\n\n'
                 f'Folder: `{destination}`\n\nGoal (paste into Instructions / goal):\n\n{spec["goal"]}\n\n'
                 f'Workers: {workers}. Request budget: {spec["calls"]}. Loop iterations: {spec["iterations"]}.\n\n'
                 f'Checks:\n\n```sh\n{check}\n```\n\nProtected paths: `tests`, `DEMO.md`, `demo.json` (one per line).\n\n'
                 '## Run from the terminal\n\n'
                 f'```sh\n{command}\n```\n\n'
                 '## What to observe\n\nBaseline checks intentionally fail until the feature is implemented. '
                 'Inspect planner shared interfaces, critic feedback, parallel worker inputs, combined checks, '
                 'and reviewer decisions. Rejected changes are rolled back; lessons feed the next plan. '
                 'A correct first attempt may finish in one loop; multiple iterations are not guaranteed. '
                 'The frontend here is server-rendered HTML, not a JavaScript framework or a running browser app. '
                 'Preparing this folder uses no API calls. Starting agents uses your configured key and budget. '
                 'Accepted changes stay in the run workspace until you apply them to this folder.\n')
        (destination / "DEMO.md").write_text(guide, encoding="utf-8")
        git(destination, "init")
        git(destination, "add", ".")
        git(destination, "-c", "user.name=GoalForge Demo", "-c", "user.email=demo@localhost",
            "commit", "-m", f"Prepare {kind} demo")
    except Exception:
        # This folder was created exclusively by this invocation.
        shutil.rmtree(destination)
        raise
    return destination


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", choices=DEMOS)
    parser.add_argument("--name", help="New folder name inside demos/")
    parser.add_argument("--workers", type=int, choices=range(1, 9))
    parser.add_argument("--list", action="store_true", help="List scenarios without creating anything")
    args = parser.parse_args(argv)
    if args.list or args.demo is None:
        print("Step 1 · Choose a demo\n")
        for i, (key, spec) in enumerate(DEMOS.items(), 1):
            print(f'{i}. {key}: {spec["title"]}\n   {spec["description"]}\n')
        if args.list:
            return 0
    if not sys.stdin.isatty() and (args.demo is None or args.name is None):
        parser.error("Noninteractive use requires --demo and --name (or use --list).")
    try:
        kind = args.demo
        while kind is None:
            choice = input("Demo name or number [2]: ").strip() or "2"
            if choice.isdigit() and 1 <= int(choice) <= len(DEMOS):
                choice = list(DEMOS)[int(choice) - 1]
            if choice in DEMOS:
                kind = choice
            else:
                print("Choose one of the demo names or numbers above.")
        print(f'\nStep 2 · Name your {kind} demo')
        name = args.name
        while True:
            name = name if name is not None else input(f"Folder name [{kind}-demo]: ").strip() or f"{kind}-demo"
            try:
                valid_name(name)
                if (PROJECT / "demos" / name).exists():
                    raise ValueError("That demo folder already exists. Choose a new name.")
                break
            except ValueError as exc:
                if args.name is not None:
                    parser.error(str(exc))
                print(exc)
                name = None
        print("\nStep 3 · Set coding worker count")
        workers = args.workers
        if workers is None and not sys.stdin.isatty():
            workers = 3
        while workers is None:
            value = input("Workers [3]: ").strip() or "3"
            if value.isdigit() and 1 <= int(value) <= 8:
                workers = int(value)
            else:
                print("Enter a number from 1 to 8.")
        destination = prepare(kind, name, workers=workers)
        print(f"\nPrepared: {destination}\nSetup instructions: {destination / 'DEMO.md'}")
        print(f"\nGoal for the web UI:\n{DEMOS[kind]['goal']}")
        print(f"\nWorkers: {workers} · Request budget: {DEMOS[kind]['calls']} · Iterations: {DEMOS[kind]['iterations']}")
        print("No LLM requests made. Open DEMO.md for the check command and full run instructions.")
        return 0
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        return 130
    except (ValueError, OSError, RuntimeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(run())
