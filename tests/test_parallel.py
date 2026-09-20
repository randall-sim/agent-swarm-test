"""Concurrency tests use barriers, not timing assumptions, to prove worker overlap."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from goalforge.cli import main
from goalforge.engine import Engine
from goalforge.provider import Budget, BudgetExceeded
from goalforge.state import create_run, git
from goalforge.swarm import validate_tasks
from goalforge.workspace import Workspace


def plan():
    return {"title": "Fix both modules", "approach": "Independent constants, no shared API changes",
            "acceptance": "Both constants match the acceptance check", "tasks": [
                {"title": "Fix left", "approach": "Set VALUE to 2", "acceptance": "left.VALUE is 2", "files": ["left.py"]},
                {"title": "Fix right", "approach": "Set VALUE to 3", "acceptance": "right.VALUE is 3", "files": ["right.py"]},
            ]}


class ParallelModel:
    """Fake LLM with genuine simultaneous calls, shared counters, and isolated histories."""
    def __init__(self, limit=80, mode="normal", barrier=True):
        self.budget = Budget(limit)
        self.mode = mode
        self.barrier = threading.Barrier(2, timeout=5) if barrier else None
        self.reached = set()
        self.lock = threading.Lock()

    def fork(self):
        return self

    def complete(self, messages, **kwargs):
        self.budget.reserve()
        context = json.loads(messages[1]["content"])
        system = messages[0]["content"]
        if "assigned_task" in context:
            task = context["assigned_task"]
            path = task["files"][0]
            tool_already_called = any(m["role"] == "assistant" for m in messages)
            if not tool_already_called:
                with self.lock:
                    self.reached.add(context["agent_id"])
                if self.barrier:
                    self.barrier.wait()  # A sequential executor cannot pass this test.
                value = 2 if path == "left.py" else 3
                if self.mode == "failed_check" and path == "right.py":
                    value = 99
                if self.mode in {"commands", "unassigned", "interrupt"}:
                    dest = "unexpected.py" if self.mode == "unassigned" and path == "left.py" else path
                    code = f"from pathlib import Path; Path({dest!r}).write_text('VALUE = {value}\\n')"
                    return {"tool": "run", "args": {"argv": [sys.executable, "-c", code]}}
                return {"tool": "write_file", "args": {"path": path, "content": f"VALUE = {value}\n\n"}}
            return {"final": {"summary": f"Fixed {path}"}}
        if "Review the proposal before execution" in system:
            return {"final": {"approved": True, "reason": "Disjoint tasks"}}
        if "Evaluate the actual changes" in system:
            return {"final": {"accept": True, "complete": True, "reason": "Both modules fixed", "lesson": "Independent fixes integrate"}}
        return {"final": plan()}


@contextmanager
def model_server(model):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            reply = model.complete(body["messages"])
            payload = json.dumps({"choices": [{"message": {"content": json.dumps(reply)}}]}).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


class ParallelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init")
        for path in ("left.py", "right.py"):
            (self.repo / path).write_text("VALUE = 0\n\n")
        (self.repo / "verify.py").write_text("import left, right\nassert left.VALUE == 2\nassert right.VALUE == 3\n")
        git(self.repo, "add", ".")
        git(self.repo, "-c", "user.name=Test", "-c", "user.email=test@localhost", "commit", "-m", "base")
        self.base = git(self.repo, "rev-parse", "HEAD")
        self.store = create_run(self.repo, self.root / "runs", "Fix both modules", [[sys.executable, "verify.py"]],
                                ["verify.py"], "Preserve checks", "test", "https://example.com/v1")
        self.workspace = Workspace(Path(self.store.read()["workspace"]), lambda _: True, protected=["verify.py"])
        self.messages = []

    def tearDown(self):
        self.tmp.cleanup()

    def execute(self, model):
        return Engine(self.store, model, self.workspace, emit=self.messages.append, workers=2).execute(1)

    def assert_clean_workers(self, state):
        self.assertEqual(state["worker_paths"], [])
        self.assertNotIn("coder-", git(self.repo, "worktree", "list", "--porcelain"))

    def test_workers_overlap_and_changes_integrate_atomically(self):
        model = ParallelModel()
        state = self.execute(model)
        self.assertEqual(state["status"], "complete", state.get("last_error"))
        self.assertEqual(model.reached, {"coder-1", "coder-2"})
        self.assertEqual(state["usage"]["requests"], 7)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), self.base)
        self.assertEqual((self.workspace.root / "left.py").read_text(), "VALUE = 2\n\n")
        self.assertEqual((self.workspace.root / "right.py").read_text(), "VALUE = 3\n\n")
        self.assertEqual(len(state["history"][-1]["workers"]), 2)
        self.assert_clean_workers(state)
        events = [json.loads(line) for line in (self.store.root / "events.jsonl").read_text().splitlines()]
        starts = [i for i, e in enumerate(events) if e["kind"] == "worker_started"]
        ends = [i for i, e in enumerate(events) if e["kind"] == "worker_finished"]
        self.assertLess(max(starts), min(ends))
        self.assertTrue(any("[coder-1]" in m for m in self.messages))
        self.assertTrue(any("[coder-2]" in m for m in self.messages))

    def test_failed_combined_check_reverts_both_workers(self):
        state = self.execute(ParallelModel(mode="failed_check"))
        self.assertEqual(state["history"][-1]["outcome"], "repair_pending")
        self.assertEqual(state["accepted_commit"], self.base)
        for path in ("left.py", "right.py"):
            self.assertEqual((self.workspace.root / path).read_text(), "VALUE = 0\n\n")
        self.assert_clean_workers(state)

    def test_integration_failure_reverts_already_applied_patch(self):
        applied = []
        def apply_failure(root, *args, **kwargs):
            if args[0] == "apply":
                applied.append(args[-1])
                if len(applied) == 2:
                    raise RuntimeError("simulated patch conflict")
            return git(root, *args, **kwargs)
        with patch("goalforge.swarm.git", side_effect=apply_failure):
            state = self.execute(ParallelModel())
        self.assertEqual(len(applied), 2)
        self.assertEqual(state["history"][-1]["outcome"], "reverted")
        self.assertIn("patch conflict", state["history"][-1]["lesson"])
        self.assertEqual(state["accepted_commit"], self.base)
        self.assertEqual((self.workspace.root / "left.py").read_text(), "VALUE = 0\n\n")
        self.assertEqual(git(self.workspace.root, "status", "--porcelain"), "")
        self.assert_clean_workers(state)

    def test_shared_budget_stops_and_resume_can_finish(self):
        state = self.execute(ParallelModel(limit=4, barrier=False))
        self.assertEqual(state["status"], "budget_exhausted")
        self.assertEqual(state["usage"]["requests"], 4)
        self.assertEqual(state["accepted_commit"], self.base)
        self.assert_clean_workers(state)
        state = self.execute(ParallelModel())
        self.assertEqual(state["status"], "complete")
        self.assertEqual(state["attempt"], 2)

    def test_command_approvals_run_on_main_thread(self):
        observed = []
        def approve(argv):
            observed.append(threading.current_thread())
            return True
        self.workspace.approve = approve
        state = self.execute(ParallelModel(mode="commands"))
        self.assertEqual(state["status"], "complete")
        self.assertEqual(observed, [threading.main_thread(), threading.main_thread()])

    def test_interrupt_during_approval_cancels_waiters(self):
        def approve(argv):
            raise KeyboardInterrupt()
        self.workspace.approve = approve
        state = self.execute(ParallelModel(mode="interrupt"))
        self.assertEqual(state["status"], "paused")
        self.assertEqual(state["accepted_commit"], self.base)
        self.assert_clean_workers(state)

    def test_unassigned_command_edits_reject_entire_batch(self):
        state = self.execute(ParallelModel(mode="unassigned"))
        self.assertEqual(state["history"][-1]["outcome"], "reverted")
        self.assertIn("unassigned", state["history"][-1]["lesson"])
        self.assertEqual(state["accepted_commit"], self.base)
        self.assertFalse((self.workspace.root / "unexpected.py").exists())
        self.assert_clean_workers(state)

    def test_crashed_worker_worktree_is_cleaned_on_resume(self):
        state = self.store.read()
        path = self.store.root / "workers" / "attempt-0" / "coder-1"
        path.parent.mkdir(parents=True)
        git(self.repo, "worktree", "add", "--detach", str(path), self.base)
        (path / "left.py").write_text("broken")
        state["worker_paths"] = [str(path)]
        self.store.write(state)
        state = self.execute(ParallelModel())
        self.assertEqual(state["status"], "complete")
        self.assertFalse(path.exists())
        self.assert_clean_workers(state)

    def test_reject_overlapping_protected_and_unsafe_assignments(self):
        for bad in ("left.py", "verify.py", "../escape", "*.py", "./right.py", ".env"):
            candidate = plan()
            candidate["tasks"][1]["files"] = [bad]
            with self.subTest(path=bad), self.assertRaises(ValueError):
                validate_tasks(candidate, self.workspace, 2)

    def test_file_tools_enforce_task_ownership(self):
        self.workspace.allowed_paths = {"left.py"}
        with self.assertRaisesRegex(ValueError, "another task"):
            self.workspace.execute("write_file", {"path": "right.py", "content": "oops"}, True)

    def test_cli_parallel_over_real_http(self):
        model = ParallelModel()
        with model_server(model) as url, patch.dict(os.environ, {"LLM_API_KEY": "fake-key"}), redirect_stdout(io.StringIO()):
            result = main(["run", "Fix both modules", "--repo", str(self.repo),
                           "--runs-dir", str(self.root / "cli-runs"), "--check", f'"{sys.executable}" verify.py',
                           "--protect", "verify.py", "--model", "test", "--base-url", url,
                           "--iterations", "1"])
        self.assertEqual(result, 0)
        self.assertEqual(model.reached, {"coder-1", "coder-2"})
        state_path = next((self.root / "cli-runs").glob("*/state.json"))
        state = json.loads(state_path.read_text())
        self.assertEqual(state["workers"], 3)  # New CLI default; planner used only two.

    def test_budget_reservations_are_atomic(self):
        budget = Budget(7)
        def reserve(_):
            try:
                budget.reserve()
                budget.record({"prompt_tokens": 10, "completion_tokens": 2})
                return True
            except BudgetExceeded:
                return False
        with ThreadPoolExecutor(max_workers=20) as pool:
            outcomes = list(pool.map(reserve, range(100)))
        self.assertEqual(sum(outcomes), 7)
        self.assertEqual(budget.used, 7)
        self.assertEqual(budget.input_tokens, 70)
        self.assertEqual(budget.output_tokens, 14)


class ParallelDemoTests(unittest.TestCase):
    def test_prepare_demo_without_api_and_validate_acceptance_suite(self):
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "demo"
            result = subprocess.run([sys.executable, str(project / "examples" / "run_parallel_demo.py"),
                                     "--prepare-only", "--demo-dir", str(target)],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("no LLM requests", result.stdout)
            self.assertEqual(git(target, "status", "--porcelain"), "")
            command = [sys.executable, "-m", "unittest", "discover", "-s", "tests"]
            baseline = subprocess.run(command, cwd=target, capture_output=True, text=True, timeout=10)
            self.assertNotEqual(baseline.returncode, 0)
            self.assertIn("Ran 13 tests", baseline.stderr)
            (target / "text_tools.py").write_text("import re\ndef slugify(text):\n    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')\n")
            (target / "stats_tools.py").write_text("import statistics\ndef median(values):\n    if not values: raise ValueError('empty')\n    return statistics.median(values)\n")
            (target / "date_tools.py").write_text("def is_leap_year(year):\n    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)\n")
            fixed = subprocess.run(command, cwd=target, capture_output=True, text=True, timeout=10)
            self.assertEqual(fixed.returncode, 0, fixed.stderr)
