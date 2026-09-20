"""Parallel coding workers: isolated snapshots, explicit ownership, atomic integration."""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from queue import Empty, Queue
from pathlib import Path, PurePosixPath
from threading import Event
from typing import TYPE_CHECKING

from .provider import BudgetExceeded, ProviderError
from .state import git, rollback
from .workspace import Workspace, sensitive

if TYPE_CHECKING:
    from .engine import Engine


PARALLEL_PLANNER = '''
Parallel mode is enabled. Your final object must also contain tasks: a list with 1 to
max_workers entries. Each task has title, approach, acceptance (nonempty strings), and
files (a nonempty list of exact repository-relative file paths, including new files).
Split independent work across multiple tasks when useful; do not duplicate the same work.
Each file may belong to exactly one task. All workers start at the same commit and cannot
see each other's unmerged changes. Define shared interfaces EXACTLY in the overall approach
BEFORE dispatch: import module and symbol names (including exceptions), method names and argument
order, return types with field names, and error/status semantics. For example, specify
`storage.Store.reserve(sku, quantity, request_id) -> (reservation_dict, created_bool)` and name
all exception classes and their defining module; "choose a shared exception vocabulary" is NOT
a contract. Every affected task must reference the same decisions. A contract file written by
one parallel worker is not visible to others until integration, so the proposal must contain
all decisions they need. If this cannot be decided now, use a single worker for coupled changes.
Only specify shared contracts needed for THIS increment. List future features as deferred work;
do not expand the increment just to satisfy a full-goal design checklist. Respect max_workers even
when it is lower than the run's configured capacity. With max_workers=1, give one worker all coupled
implementation and test files; no parallel interface agreement is needed inside that one task.
Do not assign tasks that depend on another worker finishing first: do that work in a later
iteration instead. Use one task for tightly coupled work. Never invent work just to fill slots.
Include test files in the owning task when new tests are needed. Do not assign protected paths.
'''


class WorkerCancelled(RuntimeError):
    pass


def validate_tasks(proposal: dict, workspace: Workspace, maximum: int) -> list[dict]:
    tasks = proposal.get("tasks")
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= maximum:
        raise ValueError(f"tasks must contain between 1 and {maximum} independent tasks")
    owned = set()
    for task in tasks:
        if not isinstance(task, dict) or any(not isinstance(task.get(k), str) or not task[k].strip()
                                             for k in ("title", "approach", "acceptance")):
            raise ValueError("Every task needs nonempty title, approach, and acceptance strings")
        files = task.get("files")
        if not isinstance(files, list) or not files or len(files) > 50:
            raise ValueError("Each task needs 1–50 exact file paths")
        for path in files:
            if not isinstance(path, str) or not path or path == "." or "\\" in path:
                raise ValueError("Use exact relative file paths with forward slashes")
            pure = PurePosixPath(path)
            if pure.is_absolute() or ".." in pure.parts or pure.as_posix() != path or any(c in path for c in "*?[]"):
                raise ValueError("File assignments cannot contain traversal, globs, or aliases")
            target = workspace.path(path, writing=True)
            if target.is_dir():
                raise ValueError("Assign individual files, not directories")
            # Case folding avoids ambiguous ownership on Windows filesystems.
            key = path.casefold()
            if any(key == p or key.startswith(p + "/") or p.startswith(key + "/") for p in owned):
                raise ValueError(f"Overlapping file ownership: {path}")
            owned.add(key)
    return tasks


def cleanup_workers(engine: Engine, state: dict) -> None:
    pending = state.get("worker_paths", [])
    remaining = []
    errors = []
    for value in pending:
        path = Path(value)
        if not path.resolve().is_relative_to((engine.store.root / "workers").resolve()):
            remaining.append(value)
            errors.append("Worker path is outside the run's worker directory")
            continue
        try:
            # Registration may not exist if creation was interrupted before Git ran.
            registered = git(Path(state["repo"]), "worktree", "list", "--porcelain")
            if f"worktree {path}\n" in registered + "\n":
                git(Path(state["repo"]), "worktree", "remove", "--force", str(path))
        except Exception as exc:
            remaining.append(value)
            errors.append(str(exc))
    state["worker_paths"] = remaining
    engine.store.write(state)
    if errors:
        raise RuntimeError("Worker cleanup failed: " + "; ".join(errors))


def implement_parallel(engine: Engine, state: dict, context: dict, item: dict) -> bool:
    """Return true only when every worker's patch was integrated successfully."""
    from .engine import Engine

    tasks = validate_tasks(context["proposal"], engine.workspace, engine.workers)
    worker_base = (state.get("candidate") or {}).get("commit", state["accepted_commit"])
    cancelled = Event()
    approvals = Queue()
    prepared = []
    reports = []
    pool = None
    engine.emit(f"[orchestrator] Dispatching {len(tasks)} coding worker(s); cap={engine.workers}")
    engine.store.event("dispatch", attempt=state["attempt"], tasks=tasks, workers=len(tasks))
    try:
        for index, task in enumerate(tasks, 1):
            agent_id = f"coder-{index}"
            folder = engine.store.root / "workers" / f"attempt-{state['attempt']}"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / agent_id
            # Persist the path before creating its worktree, so resume can recover it.
            state.setdefault("worker_paths", []).append(str(path))
            engine.store.write(state)
            git(Path(state["repo"]), "worktree", "add", "--detach", str(path), worker_base)
            def approve(argv, worker_id=agent_id):
                done, answer = Event(), []
                approvals.put((worker_id, argv, done, answer))
                while not done.wait(0.1):
                    if cancelled.is_set():
                        return False
                return answer[0]

            workspace = Workspace(path, approve, engine.workspace.timeout,
                                  engine.workspace.protected, engine.workspace.secret, set(task["files"]))
            worker = Engine(engine.store, engine.client.fork(), workspace, engine.steps,
                            engine.emit, agent_id=agent_id, cancelled=cancelled)
            prepared.append((worker, task, folder / f"{agent_id}.patch"))

        def code(entry):
            worker, task, patch_path = entry
            agent_id = worker.agent_id
            engine.store.event("worker_started", agent_id=agent_id, attempt=state["attempt"],
                               task=task, workspace=str(worker.workspace.root))
            engine.emit(f"[{agent_id}] START {task['title']} | files: {', '.join(task['files'])}")
            try:
                summary = worker.role("coder", {**context, "assigned_task": task,
                                              "agent_id": agent_id,
                                              "instruction": "Implement ONLY assigned_task. Other workers handle the other tasks. Read all files as needed, but change only your assigned files. Follow the shared interfaces in proposal.approach."})
                if cancelled.is_set():
                    raise WorkerCancelled("Another worker stopped the attempt")
                root = worker.workspace.root
                if git(root, "rev-parse", "HEAD") != worker_base or git(root, "rev-parse", "--abbrev-ref", "HEAD") != "HEAD":
                    raise RuntimeError("Worker changed Git HEAD or branch")
                git(root, "add", "-A")
                paths = [p for p in git(root, "diff", "--cached", "--name-only", "-z").split("\0") if p]
                invalid = [p for p in paths if p not in task["files"] or worker.workspace.is_protected(p) or sensitive(Path(p))]
                if invalid:
                    raise ValueError("Worker changed unassigned/protected paths: " + ", ".join(invalid))
                patch = git(root, "diff", "--cached", "--binary", "--full-index", "--no-ext-diff", "--no-textconv", strip=False)
                if len(patch) > 60000:
                    raise ValueError("Worker patch exceeds 60,000 characters; split the task")
                patch_path.write_text(patch, encoding="utf-8")
                report = {"agent_id": agent_id, "task": task, "summary": summary["summary"],
                          "paths": paths, "patch": str(patch_path), "status": "done"}
                engine.store.event("worker_finished", attempt=state["attempt"], **report)
                engine.emit(f"[{agent_id}] DONE ({len(paths)} changed files)")
                return report
            except Exception as exc:
                engine.store.event("worker_failed", agent_id=agent_id, attempt=state["attempt"], error=str(exc))
                engine.emit(f"[{agent_id}] STOPPED: {exc}")
                raise

        pool = ThreadPoolExecutor(max_workers=engine.workers, thread_name_prefix="goalforge-coder")
        futures = [pool.submit(code, entry) for entry in prepared]
        pending = set(futures)
        while pending:
            if engine.cancelled.is_set():
                raise WorkerCancelled("Paused by user")
            # Keep interactive approvals on the main thread, including Ctrl-C handling.
            try:
                agent_id, argv, done, answer = approvals.get_nowait()
            except Empty:
                pass
            else:
                engine.emit(f"[{agent_id}] Requesting command approval")
                engine.store.event("approval_requested", agent_id=agent_id, argv=argv, attempt=state["attempt"])
                allowed = engine.workspace.approve(argv)
                answer.append(allowed)
                done.set()
                engine.store.event("approval_resolved", agent_id=agent_id, allowed=allowed, attempt=state["attempt"])
            finished, pending = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
            for future in finished:
                reports.append(future.result())
        reports.sort(key=lambda r: r["agent_id"])
        item["workers"] = reports
        engine.emit("[orchestrator] Integrating worker patches...")
        # Deterministic application after all workers finish. Any failure reverts the whole batch.
        for report in reports:
            if report["paths"]:
                git(engine.workspace.root, "apply", "--index", report["patch"])
        item["implementation"] = {"summary": "\n".join(f"{r['agent_id']}: {r['summary']}" for r in reports)}
        engine.store.event("integration", attempt=state["attempt"], workers=reports)
        return True
    except (BudgetExceeded, ProviderError, WorkerCancelled):
        raise
    except Exception as exc:
        item.update(outcome="reverted", workers=reports, lesson=f"Parallel attempt rejected: {exc}")
        rollback(state)
        return False
    finally:
        cancelled.set()
        if pool is not None:
            # Never remove a worktree while its thread can still write to it.
            # In-flight requests/commands finish or reach their configured timeout.
            pool.shutdown(wait=True, cancel_futures=True)
        cleanup_workers(engine, state)
