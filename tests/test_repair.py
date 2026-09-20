"""Repair integration tests use real Git/worktrees and a deterministic fake model."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

from goalforge.engine import Engine
from goalforge.provider import Budget, BudgetExceeded
from goalforge.repair import clear_candidate, restore_candidate
from goalforge.state import Store, create_run, git
from goalforge.workspace import Workspace


class RepairModel:
    def __init__(self, limit=80, no_change=False, fail_repair=False):
        self.budget = Budget(limit)
        self.no_change, self.fail_repair = no_change, fail_repair
        self.contexts = []
        self.lock = threading.Lock()

    def fork(self):
        return self

    def complete(self, messages, **kwargs):
        self.budget.reserve()
        context = json.loads(messages[1]['content'])
        system = messages[0]['content']
        with self.lock:
            self.contexts.append(context)
        if 'assigned_task' in context:
            path = context['assigned_task']['files'][0]
            used = any(m['role'] == 'assistant' for m in messages)
            if used:
                if self.fail_repair and context['repair']:
                    raise BudgetExceeded('Interrupted after repair edit')
                return {'final': {'summary': 'Edited assigned module'}}
            if self.no_change:
                return {'tool': 'read_file', 'args': {'path': path}}
            if context['repair']:
                return {'tool': 'replace_text', 'args': {'path': 'right.py', 'old': 'VALUE = 99', 'new': 'VALUE = 3'}}
            return {'tool': 'write_file', 'args': {'path': path, 'content': 'VALUE = 2\n' if path == 'left.py' else 'VALUE = 99\n'}}
        if 'Review the proposal before execution' in system:
            return {'final': {'approved': True, 'reason': 'The task owns the failing module'}}
        if 'Evaluate the actual changes' in system:
            passed = all(c['returncode'] == 0 for c in context['check_results'])
            return {'final': {'accept': passed, 'complete': passed, 'reason': 'Check evidence',
                              'lesson': '' if passed else 'Only right.py is wrong; preserve left.py and change VALUE to 3'}}
        paths = ['right.py'] if context['repair'] else ['left.py', 'right.py']
        return {'final': {'title': f'Repair attempt {context["attempt"]}', 'approach': 'Preserve the existing working code',
                          'acceptance': 'Both values pass', 'tasks': [dict(title=p, approach='Fix assigned value', acceptance='Check passes', files=[p]) for p in paths]}}


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        for name in ('left.py', 'right.py'):
            (self.repo / name).write_text('VALUE = 0\n')
        (self.repo / 'verify.py').write_text("from pathlib import Path\nassert Path('left.py').read_text() == 'VALUE = 2\\n'\nassert Path('right.py').read_text() == 'VALUE = 3\\n'\n")
        git(self.repo, 'init')
        git(self.repo, 'add', '.')
        git(self.repo, '-c', 'user.name=Test', '-c', 'user.email=test@localhost', 'commit', '-m', 'initial')
        self.base = git(self.repo, 'rev-parse', 'HEAD')
        self.store = create_run(self.repo, self.root / 'runs', 'Fix both values', [[sys.executable, 'verify.py']], ['verify.py'], '', 'fake', 'http://localhost/v1')
        self.workspace = Workspace(Path(self.store.read()['workspace']), lambda _: False, protected=['verify.py'])

    def execute(self, model, iterations=1, **kwargs):
        return Engine(self.store, model, self.workspace, emit=lambda _: None, workers=2).execute(iterations, **kwargs)

    def test_targeted_second_attempt_preserves_first_worker_and_accepts_only_verified_tree(self):
        model = RepairModel()
        state = self.execute(model, 2)
        self.assertEqual(state['status'], 'complete', state.get('last_error'))
        self.assertEqual([h['outcome'] for h in state['history']], ['repair_pending', 'kept'])
        self.assertEqual(state['history'][1]['mode'], 'repair')
        self.assertEqual(len(state['history'][1]['workers']), 1)
        self.assertEqual(state['history'][1]['workers'][0]['paths'], ['right.py'])
        self.assertNotIn('candidate', state)
        self.assertEqual((self.workspace.root / 'left.py').read_text(), 'VALUE = 2\n')
        self.assertEqual((self.workspace.root / 'right.py').read_text(), 'VALUE = 3\n')
        self.assertEqual((self.repo / 'left.py').read_text(), 'VALUE = 0\n')
        self.assertEqual(git(self.workspace.root, 'rev-list', '--count', self.base + '..HEAD'), '1')
        repair = next(c['repair'] for c in model.contexts if c['repair'])
        self.assertIn('Only right.py', repair['review']['lesson'])
        self.assertTrue(any(c['current_evidence']['checks'][0]['returncode'] != 0 for c in model.contexts if c['repair']))
        delta = json.loads((self.store.root / 'attempts' / '2.json').read_text())
        self.assertNotIn('diff --git a/left.py', delta['diff'])
        self.assertIn('diff --git a/left.py', delta['review_diff'])

    def test_candidate_survives_restart_without_moving_accepted_branch(self):
        state = self.execute(RepairModel())
        self.assertEqual(state['accepted_commit'], self.base)
        self.assertEqual(git(self.workspace.root, 'rev-parse', state['branch']), self.base)
        self.assertEqual(git(self.workspace.root, 'status', '--porcelain'), '')
        self.assertIn('VALUE = 2', git(self.repo, 'show', state['candidate']['commit'] + ':left.py'))
        self.store = Store(self.store.root)  # Reload durable state as a new process would.
        state = self.execute(RepairModel())
        self.assertEqual(state['status'], 'complete')

    def test_reviewer_budget_failure_retains_unreviewed_candidate(self):
        state = self.execute(RepairModel(limit=6))
        self.assertEqual(state['status'], 'budget_exhausted')
        self.assertEqual(state['candidate']['review_status'], 'pending')
        self.assertEqual(state['accepted_commit'], self.base)
        state = self.execute(RepairModel())
        self.assertEqual(state['status'], 'complete')

    def test_interrupted_repair_keeps_previous_complete_candidate(self):
        first = self.execute(RepairModel())
        commit = first['candidate']['commit']
        state = self.execute(RepairModel(fail_repair=True))
        self.assertEqual(state['candidate']['commit'], commit)
        self.assertEqual(state['status'], 'budget_exhausted')
        self.assertEqual(git(self.workspace.root, 'status', '--porcelain'), '')
        state = self.execute(RepairModel())
        self.assertEqual(state['status'], 'complete')

    def test_unchanged_repairs_pause_and_explicit_discard_restarts_from_accepted(self):
        self.execute(RepairModel())
        state = self.execute(RepairModel(no_change=True), 6)
        self.assertEqual(state['status'], 'paused')
        self.assertEqual(state['attempt'], 3)
        self.assertIn('no substantive changes', state['last_error'])
        model = RepairModel()
        state = self.execute(model, discard_candidate=True)
        self.assertIsNone(model.contexts[0]['repair'])
        self.assertEqual(state['candidate']['rounds'], 1)
        self.assertEqual(state['accepted_commit'], self.base)

    def test_restore_refuses_mismatched_accepted_base(self):
        state = self.execute(RepairModel())
        state['candidate']['accepted_base'] = 'incorrect'
        with self.assertRaisesRegex(RuntimeError, 'does not match'):
            restore_candidate(state)
