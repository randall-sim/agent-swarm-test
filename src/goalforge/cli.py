from __future__ import annotations

import argparse
import getpass
import json
from pathlib import Path
import shlex
import sys
from threading import RLock

from .config import settings
from .engine import Engine
from .provider import Budget, Client
from .state import Store, create_run
from .workspace import Workspace


def positive(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def worker_count(value: str) -> int:
    number = positive(value)
    if number > 8:
        raise argparse.ArgumentTypeError("must be between 1 and 8")
    return number


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="goalforge", description="Turn a goal into tested changes using a knowledge-driven coding loop.")
    sub = p.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Start a goal in an isolated Git worktree")
    run.add_argument("goal", help="Concrete coding goal, including expected behavior")
    run.add_argument("--repo", type=Path, default=Path.cwd())
    run.add_argument("--check", action="append", required=True, help="Required verification command; repeat for multiple checks")
    run.add_argument("--protect", action="append", default=[], help="Repository-relative path or glob which must not change")
    run.add_argument("--directive", type=Path, help="Text file containing coding conventions and constraints")
    for command in (run, sub.add_parser("resume", help="Continue from the last accepted checkpoint")):
        if command is not run:
            command.add_argument("run_id")
        command.add_argument("--runs-dir", type=Path, default=None)
        command.add_argument("--model", default=None, help="Your provider's model ID (or LLM_MODEL)")
        command.add_argument("--base-url", default=None, help="Chat Completions API base URL, usually ending /v1")
        command.add_argument("--key-env", default="LLM_API_KEY", help="Environment variable containing the key; otherwise prompt privately")
        command.add_argument("--workers", type=worker_count, help="Concurrent coding workers, 1–8 (new runs default to 3; 1 uses sequential mode)")
        command.add_argument("--iterations", type=positive, default=5, help="Maximum attempts this invocation (default 5)")
        command.add_argument("--max-calls", type=positive, default=80, help="Maximum HTTP requests, including retries (default 80)")
        command.add_argument("--role-steps", type=positive, default=12, help="Maximum requests per role (default 12)")
        command.add_argument("--max-tokens", type=positive, default=4096, help="Maximum output tokens per request")
        command.add_argument("--timeout", type=positive, default=120, help="Timeout per command in seconds")
        command.add_argument("--api-timeout", type=positive, default=90, help="Timeout per HTTP request in seconds")
        command.add_argument("--yes", action="store_true", help="Allow all model-suggested commands without prompts (NOT sandboxed)")
    for name in ("status", "history"):
        command = sub.add_parser(name, help="Inspect saved " + name)
        command.add_argument("run_id")
        command.add_argument("--runs-dir", type=Path, default=None)
    ls = sub.add_parser("list", help="List saved runs")
    ls.add_argument("--runs-dir", type=Path, default=None)
    web = sub.add_parser("serve", help="Open the local web UI for goals and live agent activity")
    web.add_argument("repo", nargs="?", type=Path, default=Path.cwd())
    web.add_argument("--runs-dir", type=Path, default=None)
    web.add_argument("--port", type=int, default=8765)
    web.add_argument("--no-open", action="store_true", help="Print the URL without opening a browser")
    for command in sub.choices.values():
        command.add_argument("--env-file", type=Path,
                             help="Configuration file (default: .env in the current directory)")
    return p


def resolve_store(args) -> Store:
    # IDs are single path components, never paths outside the chosen store.
    if not args.run_id or Path(args.run_id).name != args.run_id or args.run_id in {".", ".."}:
        raise ValueError("Invalid run ID")
    store = Store(args.runs_dir.resolve() / args.run_id)
    if not store.file.is_file():
        raise ValueError(f"Run not found: {args.run_id}")
    return store


def report(state: dict) -> None:
    print(f"\nRun: {state['id']}\nStatus: {state['status']}\nGoal: {state['goal']}")
    print(f"Coding worker limit: {state.get('workers', 1)}")
    print(f"Branch: {state['branch']}\nWorkspace: {state['workspace']}")
    print(f"Accepted commit: {state['accepted_commit']}\nUsage: {json.dumps(state['usage'])}")
    if state.get("last_error") and state["status"] in {"error", "paused", "budget_exhausted"}:
        print("Last stop: " + state["last_error"])
    if state["status"] != "complete":
        print(f"Continue: goalforge resume {state['id']} (use the same --runs-dir if customized)")
    print(f"Review from your repository: git diff {state['start_commit']}..{state['branch']}")
    print(f"When satisfied, integrate from your original branch: git merge --ff-only {state['branch']}")


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        config = settings(args.env_file)
        if args.runs_dir is None:
            args.runs_dir = Path(config.get("GOALFORGE_HOME") or str(Path.home() / ".goalforge")) / "runs"
        if args.command == "serve":
            from .web import serve
            return serve(args.repo, args.runs_dir, config, args.port, not args.no_open)
        if args.command == "list":
            for path in sorted(args.runs_dir.glob("*/state.json")):
                state = json.loads(path.read_text(encoding="utf-8"))
                print(f"{state['id']}  {state['status']:18}  {state['goal'][:100]}")
            return 0
        if args.command in {"status", "history"}:
            state = resolve_store(args).read()
            if args.command == "status":
                report(state)
            else:
                print(json.dumps(state["history"], indent=2, ensure_ascii=False))
            return 0
        store = resolve_store(args) if args.command == "resume" else None
        prior = store.read() if store else {}
        workers = args.workers if args.workers is not None else prior.get("workers", 1 if prior else 3)
        model = args.model or config.get("LLM_MODEL") or prior.get("model")
        base_url = args.base_url or config.get("LLM_BASE_URL") or prior.get("base_url")
        if not model or not base_url:
            raise ValueError("Set --model and --base-url (or LLM_MODEL and LLM_BASE_URL in .env).")
        key = config.get(args.key_env)
        if not key:
            if not sys.stdin.isatty():
                raise ValueError(f"Set {args.key_env} in .env or the environment; a noninteractive session cannot prompt for a key.")
            key = getpass.getpass("LLM API key (hidden, not saved): ")
        if not key:
            raise ValueError("An API key is required; use a dummy value for a local server without authentication.")
        client = Client(base_url, model, key, Budget(args.max_calls), args.api_timeout, args.max_tokens)
        if store is None:
            if not args.goal.strip() or len(args.goal) > 16000:
                raise ValueError("Provide a nonempty goal under 16,000 characters")
            checks = [shlex.split(c) for c in args.check]
            if any(not c for c in checks):
                raise ValueError("Verification commands cannot be empty")
            if any(Path(p).is_absolute() or ".." in Path(p).parts for p in args.protect):
                raise ValueError("Protected paths must be relative to the repository")
            directive = args.directive.read_text(encoding="utf-8") if args.directive else "Follow existing project conventions. Make focused, maintainable changes."
            if len(directive) > 16000:
                raise ValueError("Keep the directive under 16,000 characters")
            store = create_run(args.repo, args.runs_dir, args.goal, checks, args.protect, directive, model, base_url)
        store.secret = key
        state = store.read()

        terminal_lock = RLock()

        def emit(message: str) -> None:
            with terminal_lock:
                print(message, flush=True)

        def approve(command: list[str]) -> bool:
            if args.yes:
                return True
            if not sys.stdin.isatty():
                print("Denied model command in noninteractive mode; use --yes to allow commands.")
                return False
            with terminal_lock:
                return input("Run in worktree " + json.dumps(command) + "? [y/N] ").strip().lower() in {"y", "yes"}

        print(f"Run ID: {state['id']}\nWorking in: {state['workspace']}")
        print("Your configured checks run automatically. Model commands " +
              ("are authorized by --yes." if args.yes else "require approval."))
        workspace = Workspace(Path(state["workspace"]), approve, args.timeout, state["protected"], key)
        emit(f"Coding worker limit: {workers} (one shared request budget)")
        result = Engine(store, client, workspace, args.role_steps, emit, workers=workers).execute(args.iterations)
        report(result)
        return 0 if result["status"] == "complete" else (1 if result["status"] == "error" else 2)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
