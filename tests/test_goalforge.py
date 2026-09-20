from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from goalforge.cli import main
from goalforge.engine import Engine
from goalforge.provider import Budget, BudgetExceeded, Client, ProviderError
from goalforge.state import Store, create_run, git
from goalforge.workspace import Workspace, run_process


@contextmanager
def server(responses, status=200):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append({"path": self.path, "authorization": self.headers.get("Authorization"),
                         "body": json.loads(self.rfile.read(int(self.headers["Content-Length"])))})
            reply = responses.pop(0) if responses else {"final": {}}
            body = json.dumps({"choices": [{"message": {"content": json.dumps(reply)}}],
                               "usage": {"prompt_tokens": 10, "completion_tokens": 5}}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}/v1", seen
    finally:
        httpd.shutdown()
        thread.join()
        httpd.server_close()


class ScriptedClient:
    def __init__(self, replies, limit=80):
        self.replies = iter(replies)
        self.budget = Budget(limit)

    def complete(self, messages, **kwargs):
        if self.budget.used >= self.budget.limit:
            raise BudgetExceeded("Test budget exhausted")
        self.budget.used += 1
        return next(self.replies)


def proposal(title="Fix the value"):
    return {"final": {"title": title, "approach": "Change value to two", "acceptance": "Check exits zero"}}


def replies(value="2\n", accept=True, complete=True):
    return [proposal(), {"final": {"approved": True, "reason": "Small, testable fix"}},
            {"tool": "write_file", "args": {"path": "value.txt", "content": value}},
            {"final": {"summary": "Changed value"}},
            {"final": {"accept": accept, "complete": complete, "reason": "Reviewed result",
                       "lesson": "The expected value is two"}}]


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.workspace = Workspace(self.root, lambda _: False, protected=["tests/*"])

    def tearDown(self):
        self.tmp.cleanup()

    def test_traversal_private_and_symlink_blocked(self):
        for path in ("../outside", ".git/config", ".GIT/config", ".env", ".ENV", "secrets.key"):
            with self.assertRaises(ValueError):
                self.workspace.path(path)
        try:
            (self.root / "alias").symlink_to(self.root.parent, target_is_directory=True)
        except OSError:
            self.skipTest("Symlinks unavailable")
        with self.assertRaises(ValueError):
            self.workspace.path("alias/anything")

    def test_readonly_protected_and_unique_replacement(self):
        with self.assertRaises(ValueError):
            self.workspace.execute("write_file", {"path": "x", "content": "hi"}, False)
        with self.assertRaises(ValueError):
            self.workspace.execute("write_file", {"path": "tests/x", "content": "hi"}, True)
        (self.root / "x").write_text("a a")
        with self.assertRaises(ValueError):
            self.workspace.execute("replace_text", {"path": "x", "old": "a", "new": "b"}, True)
        self.assertEqual((self.root / "x").read_text(), "a a")

    def test_denied_command_does_not_run(self):
        result = self.workspace.execute("run", {"argv": [sys.executable, "-c", "raise Exception()"]}, True)
        self.assertTrue(result["denied"])

    def test_timeout_and_output_limit(self):
        result = run_process([sys.executable, "-c", "import time; time.sleep(10)"], self.root, .05)
        self.assertTrue(result["timed_out"])
        result = run_process([sys.executable, "-c", "print('x' * 30000)"], self.root, 10)
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(result["output"]), 16000)

    def test_key_not_in_child_environment(self):
        with patch.dict(os.environ, {"CUSTOM_LLM_CREDENTIAL": "secret-value", "LLM_API_KEY": "secret-value"}):
            result = run_process([sys.executable, "-c", "import os; print(os.getenv('CUSTOM_LLM_CREDENTIAL')); print(os.getenv('LLM_API_KEY'))"],
                                 self.root, 10, "secret-value")
        self.assertEqual(result["output"].strip(), "None\nNone")


class ProviderTests(unittest.TestCase):
    def test_http_contract_and_usage(self):
        with server([proposal()]) as (url, seen):
            client = Client(url, "test-model", "test-key", Budget(3))
            self.assertEqual(client.complete([{"role": "user", "content": "hi"}]), proposal())
            self.assertEqual(seen[0]["path"], "/v1/chat/completions")
            self.assertEqual(seen[0]["body"]["max_tokens"], 4096)
            self.assertEqual(seen[0]["authorization"], "Bearer test-key")
            self.assertEqual(client.budget.input_tokens, 10)
            self.assertEqual(client.budget.used, 1)

    def test_openai_uses_completion_token_parameter(self):
        client = Client("https://api.openai.com/v1", "gpt-5.4-mini", "fake-key", Budget(1))
        response = {"choices": [{"message": {"content": json.dumps(proposal())}}]}
        with patch.object(client.opener, "open", return_value=io.BytesIO(json.dumps(response).encode())) as mocked:
            client.complete([{"role": "user", "content": "Return JSON"}])
        request = mocked.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, "https://api.openai.com/v1/chat/completions")
        self.assertEqual(body["max_completion_tokens"], 4096)
        self.assertNotIn("max_tokens", body)

    def test_retry_obeys_budget(self):
        with server([], status=429) as (url, seen), patch("goalforge.provider.RateGate.wait"):
            client = Client(url, "test", "test-key", Budget(2))
            with self.assertRaises(BudgetExceeded):
                client.complete([])
            self.assertEqual(len(seen), 2)

    def test_auth_failure_and_remote_http(self):
        with server([], status=401) as (url, _):
            with self.assertRaisesRegex(ProviderError, "401"):
                Client(url, "test", "secret", Budget(2)).complete([])
        with self.assertRaises(ValueError):
            Client("http://example.com/v1", "test", "secret", Budget(2))


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init")
        (self.repo / "value.txt").write_text("1\n")
        (self.repo / "verify.py").write_text("from pathlib import Path\nassert Path('value.txt').read_text().strip() == '2'\n")
        git(self.repo, "add", ".")
        git(self.repo, "-c", "user.name=Test", "-c", "user.email=test@localhost", "commit", "-m", "initial")
        self.initial = git(self.repo, "rev-parse", "HEAD")
        self.store = create_run(self.repo, self.root / "runs", "Make value two",
                                [[sys.executable, "verify.py"]], ["verify.py"], "Keep tests intact", "fake", "https://example.com/v1")
        self.workspace = Workspace(Path(self.store.read()["workspace"]), lambda _: False, protected=["verify.py"])

    def tearDown(self):
        self.tmp.cleanup()

    def run_engine(self, responses, iterations=1, limit=80):
        return Engine(self.store, ScriptedClient(responses, limit), self.workspace, emit=lambda _: None).execute(iterations)

    def test_keeps_verified_fix_without_touching_original(self):
        state = self.run_engine(replies())
        self.assertEqual(state["status"], "complete")
        self.assertNotEqual(state["accepted_commit"], self.initial)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), self.initial)
        self.assertEqual((self.repo / "value.txt").read_text(), "1\n")
        self.assertEqual((self.workspace.root / "value.txt").read_text(), "2\n")
        self.assertEqual(state["history"][-1]["outcome"], "kept")

    def test_failing_checks_override_optimistic_reviewer(self):
        state = self.run_engine(replies("3\n"))
        self.assertEqual(state["status"], "budget_exhausted")
        self.assertEqual(state["history"][-1]["outcome"], "reverted")
        self.assertEqual((self.workspace.root / "value.txt").read_text(), "1\n")
        self.assertEqual(state["accepted_commit"], self.initial)

    def test_reviewer_can_reject_passing_checks(self):
        state = self.run_engine(replies(accept=False))
        self.assertEqual(state["history"][-1]["outcome"], "reverted")
        self.assertEqual(state["accepted_commit"], self.initial)

    def test_planning_stall_pauses_early_and_can_resume(self):
        responses = []
        for i in range(8):
            responses += [proposal(f"Plan {i}"), {"final": {"approved": False, "reason": "Specify shared errors"}}]
        state = self.run_engine(responses, iterations=8)
        self.assertEqual(state["status"], "paused")
        self.assertEqual(state["attempt"], 3)
        self.assertEqual(state["usage"]["requests"], 6)
        self.assertIn("Planning stalled", state["last_error"])
        self.assertEqual(state["accepted_commit"], self.initial)
        state = self.run_engine(replies())
        self.assertEqual(state["status"], "complete")
        self.assertNotIn("last_error", state)

    def test_review_feedback_reaches_next_plan_with_checkpoint_restored(self):
        first = replies(accept=False)
        second = replies()
        second[0] = proposal("Revised plan addressing review")
        state = self.run_engine(first + second, iterations=2)
        self.assertEqual(state["status"], "complete")
        self.assertEqual([h["outcome"] for h in state["history"]], ["reverted", "kept"])
        events = [json.loads(line) for line in (self.store.root / "events.jsonl").read_text().splitlines()]
        planner = next(e for e in events if e["kind"] == "agent_context" and e["role"] == "planner" and e["attempt"] == 2)
        context = json.loads(planner["messages"][1]["content"])
        self.assertEqual(context["recent_history"][0]["feedback_source"], "reviewer / execution")
        self.assertEqual(context["recent_history"][0]["lesson"], "The expected value is two")
        critic = next(e for e in events if e["kind"] == "agent_context" and e["role"] == "critic")
        self.assertIn("NEVER reject solely", critic["messages"][0]["content"])
        self.assertIn("Pre-implementation", json.loads(critic["messages"][1]["content"])["phase"])

    def test_rate_limit_exhaustion_pauses_without_accepting_changes(self):
        from goalforge.provider import RateLimitExceeded
        with patch.object(ScriptedClient, "complete", side_effect=RateLimitExceeded("Resume later")):
            state = self.run_engine([])
        self.assertEqual(state["status"], "paused")
        self.assertEqual(state["accepted_commit"], self.initial)
        self.assertEqual(state["last_error"], "Resume later")

    def test_budget_rollback_and_resume(self):
        state = self.run_engine(replies(), limit=3)
        self.assertEqual(state["status"], "budget_exhausted")
        self.assertEqual((self.workspace.root / "value.txt").read_text(), "1\n")
        state = self.run_engine(replies())
        self.assertEqual(state["status"], "complete")
        self.assertEqual(state["attempt"], 2)
        self.assertEqual(state["usage"]["requests"], 8)

    def test_critic_rejection_and_duplicate_memory(self):
        state = self.run_engine([proposal(), {"final": {"approved": False, "reason": "Need a better idea"}}, proposal()], iterations=2)
        self.assertEqual(len(state["history"]), 2)
        self.assertIn("already tried", state["history"][-1]["lesson"])
        self.assertEqual(state["usage"]["requests"], 3)

    def test_partial_progress_survives_later_failure(self):
        state = self.run_engine(replies(complete=False))
        accepted = state["accepted_commit"]
        self.assertNotEqual(accepted, self.initial)
        state = self.run_engine(replies("3\n"))
        self.assertEqual(state["accepted_commit"], accepted)
        self.assertEqual((self.workspace.root / "value.txt").read_text(), "2\n")

    def test_protected_change_via_command_is_rejected(self):
        self.workspace.approve = lambda _: True
        script = "from pathlib import Path; Path('verify.py').write_text('pass\\n')"
        responses = [proposal(), {"final": {"approved": True, "reason": "Proceed"}},
                     {"tool": "run", "args": {"argv": [sys.executable, "-c", script]}},
                     {"final": {"summary": "Changed verification"}}]
        state = self.run_engine(responses)
        self.assertEqual(state["history"][-1]["outcome"], "reverted")
        self.assertIn("protected", state["history"][-1]["lesson"])
        self.assertIn("assert", (self.workspace.root / "verify.py").read_text())

    def test_wrong_branch_is_not_reset(self):
        git(self.workspace.root, "checkout", "-b", "unexpected-branch")
        state = self.run_engine([])
        self.assertEqual(state["status"], "error")
        self.assertIn("refusing to reset", state["last_error"])
        self.assertEqual(git(self.workspace.root, "symbolic-ref", "--short", "HEAD"), "unexpected-branch")

    def test_dirty_original_refused(self):
        (self.repo / "value.txt").write_text("user edit")
        with self.assertRaisesRegex(ValueError, "Commit or stash"):
            create_run(self.repo, self.root / "other", "goal", [["true"]], [], "", "fake", "https://example.com")

    def test_run_lock_and_secret_redaction(self):
        with self.store.lock():
            with self.assertRaises(RuntimeError):
                with Store(self.store.root).lock():
                    pass
        self.store.secret = 'secret"key'
        self.store.event("test", value='a secret"key b')
        event = json.loads((self.store.root / "events.jsonl").read_text().splitlines()[-1])
        self.assertEqual(event["value"], "a [REDACTED] b")

    def test_cli_end_to_end_with_dotenv(self):
        with server(replies()) as (url, seen):
            env_file = self.root / '.env'
            env_file.write_text(f'LLM_API_KEY=dotenv-key\nLLM_MODEL=dotenv-model\nLLM_BASE_URL={url}\n')
            with patch.dict(os.environ, {'PATH': os.environ.get('PATH', '')}, clear=True):
                result = main(["run", "Make value two", "--repo", str(self.repo),
                               "--runs-dir", str(self.root / "dotenv-runs"), "--check",
                               f'"{sys.executable}" verify.py', "--protect", "verify.py",
                               "--env-file", str(env_file), "--iterations", "1", "--workers", "1"])
            self.assertEqual(result, 0)
            self.assertEqual(seen[0]["authorization"], "Bearer dotenv-key")
            self.assertEqual(seen[0]["body"]["model"], "dotenv-model")
            for path in (self.root / "dotenv-runs").glob("*/*.json*"):
                self.assertNotIn("dotenv-key", path.read_text())

    def test_cli_end_to_end_with_real_http(self):
        with server(replies()) as (url, seen), patch.dict(os.environ, {"LLM_API_KEY": "fake-key"}):
            result = main(["run", "Make value two", "--repo", str(self.repo),
                           "--runs-dir", str(self.root / "cli-runs"), "--check",
                           f'"{sys.executable}" verify.py', "--protect", "verify.py",
                           "--base-url", url, "--model", "test", "--iterations", "1", "--workers", "1"])
        self.assertEqual(result, 0)
        self.assertEqual(len(seen), 5)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), self.initial)


if __name__ == "__main__":
    unittest.main()
