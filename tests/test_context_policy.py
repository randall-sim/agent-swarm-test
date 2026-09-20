"""Regression cases derived from stalled reservation runs, with no paid calls."""
import json
import unittest

import test_goalforge as fixtures
from goalforge.context import retrieve_history
from goalforge.engine import Engine
from goalforge.state import git


class ContextPolicyTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.EngineTests()
        self.case.setUp()
        self.addCleanup(self.case.tearDown)
        self.engine = Engine(self.case.store, fixtures.ScriptedClient([]), self.case.workspace,
                             emit=lambda _: None)

    def test_coder_has_thirty_steps_instead_of_twelve(self):
        read = {'tool': 'read_file', 'args': {'path': 'value.txt'}}
        responses = [read] * 29 + [{'final': {'summary': 'Finished on step thirty'}}]
        engine = Engine(self.case.store, fixtures.ScriptedClient(responses), self.case.workspace,
                        emit=lambda _: None)
        self.assertEqual(engine.role('coder', {'attempt': 1})['summary'], 'Finished on step thirty')
        self.assertEqual(engine.steps, 30)
        self.assertEqual(engine.coder_steps, 30)

    def accepted(self):
        engine = Engine(self.case.store, fixtures.ScriptedClient(fixtures.replies(complete=False)),
                        self.case.workspace, emit=lambda _: None)
        return engine.execute(1)

    def test_accepted_checkpoint_excludes_stale_baseline_and_old_plans(self):
        state = self.accepted()
        self.assertNotEqual(state['baseline'][0]['returncode'], 0)
        context = self.engine.context(state)
        self.assertNotIn('baseline', context)
        self.assertEqual(context['recent_history'], [])
        self.assertEqual(context['progress']['mode'], 'advance')
        self.assertEqual(context['current_evidence']['checks'][0]['returncode'], 0)
        self.assertNotIn('failure_tail', context['current_evidence']['checks'][0])
        self.assertEqual(context['settled']['attempt'], 1)
        self.assertEqual(context['history_catalog']['attempts'], [{'attempt': 1, 'outcome': 'kept'}])
        # Old persisted runs lacking check results must not revive failed baselines.
        del state['history'][0]['check_results']
        context = self.engine.context(state)
        self.assertEqual(context['progress']['mode'], 'reassess')
        self.assertEqual(context['current_evidence']['checks'], [])

    def test_reopening_settled_work_requires_evidence(self):
        context = self.engine.context(self.accepted())
        proposal = dict(increment_kind='repair', observable_change='Fix reservations', reopen_evidence='')
        with self.assertRaisesRegex(ValueError, 'new concrete evidence'):
            self.engine.validate_decision('planner', proposal, context)
        proposal.update(reopen_evidence='Old baseline failed', reopen_source='current_check', reopen_reference='0')
        with self.assertRaisesRegex(ValueError, 'CURRENT check'):
            self.engine.validate_decision('planner', proposal, context)
        proposal.update(reopen_source='source_defect', reopen_reference='value.txt', reopen_observation='2',
                        reopen_evidence='New user requirement needs value three instead of two')
        self.engine.validate_decision('planner', proposal, context)
        proposal.update(increment_kind='advance', observable_change='Implement atomic orders and integration tests')
        self.engine.validate_decision('planner', proposal, context)

    def test_new_files_and_single_worker_are_not_dependency_blockers(self):
        context = {'proposal': {'tasks': [{'files': ['new_api.py', 'tests_extra/test_new.py']}]}}
        rejection = dict(approved=False, blocker_kind='shared_interface', evidence='API and tests need agreement')
        with self.assertRaisesRegex(ValueError, 'single worker'):
            self.engine.validate_decision('critic', rejection, context)
        rejection.update(blocker_kind='missing_directory', evidence='tests_extra does not exist')
        with self.assertRaisesRegex(ValueError, 'Missing destination'):
            self.engine.validate_decision('critic', rejection, context)
        rejection.update(blocker_kind='requirement', evidence='tests_extra does not exist yet')
        with self.assertRaisesRegex(ValueError, 'can be created'):
            self.engine.validate_decision('critic', rejection, context)
        self.engine.validate_decision('critic', dict(approved=True, blocker_kind='none', evidence=''), context)

    def test_noop_and_cosmetic_edits_cannot_advance_accepted_commit(self):
        state = self.accepted()
        accepted = state['accepted_commit']
        plan = fixtures.proposal('Pretend to improve reservations')
        plan['final'].update(increment_kind='advance', observable_change='Improve reservations', reopen_evidence='')
        response = [plan, {'final': {'approved': True, 'reason': 'Okay'}},
                    {'tool': 'write_file', 'args': {'path': 'value.txt', 'content': '2'}},
                    {'final': {'summary': 'Removed newline'}}]
        state = Engine(self.case.store, fixtures.ScriptedClient(response), self.case.workspace,
                       emit=lambda _: None).execute(1)
        self.assertEqual(state['history'][-1]['outcome'], 'no_progress')
        self.assertEqual(state['accepted_commit'], accepted)
        self.assertNotIn('candidate', state)
        self.assertEqual((self.case.workspace.root / 'value.txt').read_text(), '2\n')

    def test_cosmetic_python_detection_preserves_semantic_whitespace(self):
        root = self.case.workspace.root
        path = root / 'example.py'
        path.write_text('def f():\n    return "a b"\n')
        git(root, 'add', '.')
        git(root, '-c', 'user.name=Test', '-c', 'user.email=test@localhost', 'commit', '-m', 'fixture')
        base = git(root, 'rev-parse', 'HEAD')
        path.write_text('# Explanation\ndef f():\n    return "a b"')
        git(root, 'add', '.')
        self.assertFalse(self.engine.substantive_changes(root, base))
        path.write_text('def f():\n    return "ab"\n')
        git(root, 'add', '.')
        self.assertTrue(self.engine.substantive_changes(root, base))

    def test_archived_history_is_paginated_and_retrieved_into_conversation(self):
        state = self.accepted()
        first = retrieve_history(self.case.store, dict(attempt=0, section='checks', start=0, count=30), 2)
        second = retrieve_history(self.case.store, dict(attempt=0, section='checks', start=30, count=12000), 2)
        original = json.loads(first['text'] + second['text'])
        self.assertEqual(original, state['baseline'])
        with self.assertRaises(ValueError):
            retrieve_history(self.case.store, dict(attempt=2, section='events'), 2)
        response = [
            {'tool': 'retrieve_history', 'args': dict(attempt=1, section='review', start=0, count=6000)},
            {'final': dict(title='Add orders', approach='New feature', acceptance='Order tests',
                           increment_kind='advance', observable_change='Atomic order creation', reopen_evidence='')},
        ]
        engine = Engine(self.case.store, fixtures.ScriptedClient(response), self.case.workspace, emit=lambda _: None)
        state['attempt'] = 2
        engine.role('planner', engine.context(state))
        events = [json.loads(l) for l in (self.case.store.root / 'events.jsonl').read_text().splitlines()]
        snapshots = [e for e in events if e['kind'] == 'agent_context' and e['attempt'] == 2]
        self.assertEqual(len(snapshots[0]['messages']), 2)
        self.assertIn('Reviewed result', snapshots[1]['messages'][-1]['content'])
        self.assertTrue(any(e['kind'] == 'history_retrieved' for e in events))
        self.assertIn('archived', snapshots[1]['context_policy'])

    def test_interruption_does_not_reset_planning_fallback(self):
        state = self.case.store.read()
        state['history'] = [dict(attempt=1, outcome='rejected', base=state['accepted_commit'], lesson='A'),
                            dict(attempt=2, outcome='interrupted', lesson='Budget'),
                            dict(attempt=3, outcome='rejected', base=state['accepted_commit'], lesson='B')]
        context = self.engine.context(state)
        self.assertEqual(context['planning']['max_workers'], 1)
        self.assertTrue(context['planning']['advisory_critic'])
