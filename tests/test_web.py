"""Exercise real HTTP routes and engine/worktrees without paid API calls."""
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from goalforge.state import git
from goalforge.web import WebApp, Server
from test_parallel import ParallelModel


class WebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        git(self.repo, 'init')
        for name in ('left.py', 'right.py'):
            (self.repo / name).write_text('VALUE = 0\n\n')
        (self.repo / 'verify.py').write_text('import left, right\nassert left.VALUE == 2\nassert right.VALUE == 3\n')
        git(self.repo, 'add', '.')
        git(self.repo, '-c', 'user.name=Test', '-c', 'user.email=test@localhost', 'commit', '-m', 'base')
        self.base = git(self.repo, 'rev-parse', 'HEAD')
        self.app = WebApp(self.repo, self.root / 'runs', {'LLM_API_KEY': 'secret-test-key', 'LLM_MODEL': 'test', 'LLM_BASE_URL': 'https://example.com/v1'})
        self.server = Server(('127.0.0.1', 0), self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.app.cancelled.set()
        if self.app.thread:
            self.app.thread.join(10)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def request(self, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        supplied = {'X-GoalForge-Token': self.app.token, 'Content-Type': 'application/json'}
        supplied.update(headers or {})
        connection.request('GET' if body is None else 'POST', path,
                           None if body is None else json.dumps(body), supplied)
        response = connection.getresponse()
        content = response.read().decode()
        status = response.status
        kind = response.getheader('Content-Type')
        connection.close()
        return status, json.loads(content) if 'application/json' in kind else content

    def start(self):
        status, result = self.request('/api/start', {'goal': 'Fix both modules', 'repo': str(self.repo),
                                                    'checks': f'{sys.executable} verify.py', 'workers': 2,
                                                    'iterations': 1, 'protected': 'verify.py'})
        self.assertEqual(status, 200, result)
        return result['id']

    def finish(self):
        self.app.thread.join(10)
        self.assertFalse(self.app.thread.is_alive())

    def test_browser_assets_and_api_require_local_authenticated_requests(self):
        status, html = self.request('/')
        self.assertEqual(status, 200)
        self.assertIn('/app.js', html)
        for path in ('/app.js', '/style.css'):
            self.assertEqual(self.request(path)[0], 200)
        self.assertEqual(self.request('/api/config', headers={'X-GoalForge-Token': ''})[0], 403)
        self.assertEqual(self.request('/api/config', headers={'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.request('/', headers={'Host': 'evil.example'})[0], 403)
        status, config = self.request('/api/config')
        self.assertTrue(config['key_configured'])
        self.assertNotIn('secret-test-key', json.dumps(config))

    def test_parallel_run_trace_files_diff_and_explicit_integration(self):
        with patch('goalforge.web.Client', return_value=ParallelModel()):
            run_id = self.start()
            self.finish()
        status, snapshot = self.request('/api/run?id=' + run_id)
        self.assertEqual(snapshot['state']['status'], 'complete', snapshot)
        self.assertFalse(snapshot['running'])
        self.assertTrue(any(e['kind'] == 'dispatch' for e in snapshot['events']))
        starts = [e for e in snapshot['events'] if e['kind'] == 'worker_started']
        self.assertEqual(len(starts), 2)
        self.assertEqual(self.request('/api/run?id=' + run_id + '&cursor=' + str(snapshot['cursor']))[1]['events'], [])
        self.assertIn('VALUE = 2', self.request('/api/file?id=' + run_id + '&path=left.py')[1]['text'])
        self.assertIn('+VALUE = 2', self.request('/api/diff?id=' + run_id)[1]['diff'])
        self.assertEqual(git(self.repo, 'rev-parse', 'HEAD'), self.base)
        for path in ('.env', '../state.json', '/etc/passwd'):
            self.assertEqual(self.request('/api/file?id=' + run_id + '&path=' + path)[0], 400)
        self.assertEqual(self.request('/api/integrate', {'id': run_id})[0], 200)
        self.assertEqual((self.repo / 'left.py').read_text(), 'VALUE = 2\n\n')

    def test_approval_can_be_denied_and_pause_unblocks_waiting_run(self):
        with patch('goalforge.web.Client', return_value=ParallelModel(mode='commands')):
            run_id = self.start()
            deadline = time.monotonic() + 6
            approval = None
            while time.monotonic() < deadline:
                snapshot = self.request('/api/run?id=' + run_id)[1]
                if snapshot['approvals']:
                    approval = snapshot['approvals'][0]
                    break
                time.sleep(.02)
            self.assertIsNotNone(approval)
            self.assertEqual(self.request('/api/start', {})[0], 400)
            self.assertEqual(self.request('/api/approve', {'id': approval['id'], 'allow': False})[0], 200)
            self.assertEqual(self.request('/api/pause', {'id': run_id})[0], 200)
            self.finish()
        snapshot = self.request('/api/run?id=' + run_id)[1]
        self.assertEqual(snapshot['state']['status'], 'paused', snapshot)
        self.assertEqual(snapshot['state']['accepted_commit'], self.base)
        self.assertEqual(snapshot['approvals'], [])
        self.assertNotIn('coder-', git(self.repo, 'worktree', 'list', '--porcelain'))
        with patch('goalforge.web.Client', return_value=ParallelModel()):
            status, result = self.request('/api/resume', {'id': run_id, 'instructions': 'Keep both changes independent', 'workers': 2, 'iterations': 1})
            self.assertEqual(status, 200, result)
            self.finish()
        snapshot = self.request('/api/run?id=' + run_id)[1]
        self.assertEqual(snapshot['state']['status'], 'complete', snapshot)
        self.assertIn('Keep both changes independent', snapshot['state']['goal'])
        self.assertTrue(any(e['kind'] == 'user_instruction' for e in snapshot['events']))

    def test_approve_commands_through_http(self):
        with patch('goalforge.web.Client', return_value=ParallelModel(mode='commands')):
            run_id = self.start()
            deadline = time.monotonic() + 8
            approvals = set()
            while self.app.busy() and time.monotonic() < deadline:
                snapshot = self.request('/api/run?id=' + run_id)[1]
                for approval in snapshot['approvals']:
                    if approval['id'] not in approvals:
                        self.assertEqual(self.request('/api/approve', {'id': approval['id'], 'allow': True})[0], 200)
                        approvals.add(approval['id'])
                time.sleep(.02)
            self.finish()
        self.assertEqual(len(approvals), 2)
        self.assertEqual(self.app.store(run_id).read()['status'], 'complete')

    def test_partial_event_line_not_consumed_and_key_redacted(self):
        with patch('goalforge.web.Client', return_value=ParallelModel()):
            run_id = self.start()
            self.finish()
        store = self.app.store(run_id)
        snapshot = self.app.snapshot(run_id)
        path = store.root / 'events.jsonl'
        with path.open('ab') as stream:
            stream.write(b'{"kind":"test", "message":"secret-test-key"')
        partial = self.app.snapshot(run_id, snapshot['cursor'])
        self.assertEqual(partial['events'], [])
        self.assertEqual(partial['cursor'], snapshot['cursor'])
        with path.open('ab') as stream:
            stream.write(b'}\n')
        result = self.app.snapshot(run_id, partial['cursor'])
        self.assertEqual(result['events'][0]['message'], '[REDACTED]')


if __name__ == '__main__':
    unittest.main()
