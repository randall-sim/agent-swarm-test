"""Progression regressions: accept a milestone, then implement something new."""
import json
import sys
import unittest

from goalforge.engine import Engine, decode_reply
from goalforge.progress import next_step
from goalforge.tool_schemas import definitions
import test_goalforge as fixtures
from test_goalforge import ScriptedClient, replies, proposal


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.EngineTests()
        self.case.setUp()
        self.addCleanup(self.case.tearDown)

    def run_model(self, responses, iterations=1):
        return Engine(self.case.store, ScriptedClient(responses), self.case.workspace,
                      emit=lambda _: None).execute(iterations)

    def review(self, complete=False):
        return dict(accept=True, complete=complete, reason="Sound increment", lesson="Next capability",
                    defects="", remaining_work="" if complete else "Add order listing",
                    next_increment="" if complete else "Implement order listing in orders.py and add its test")

    def test_partial_acceptance_advances_across_resume(self):
        first = replies(complete=False)
        first[-1] = {"final": self.review()}
        state = self.run_model(first)
        self.assertEqual(state['history'][-1]['outcome'], 'kept')
        self.assertNotIn('candidate', state)
        self.assertEqual(next_step(state)['mode'], 'advance')
        second = [proposal('Add order listing'), {'final': {'approved': True, 'reason': 'New capability'}},
                  {'tool': 'write_file', 'args': {'path': 'orders.py', 'content': 'def list_orders(): return []\n'}},
                  {'final': {'summary': 'Added listing'}}, {'final': self.review(True)}]
        second[0]["final"].update(increment_kind="advance", observable_change="Add order listing verified by its test", reopen_evidence="")
        state = self.run_model(second)
        self.assertEqual(state['status'], 'complete')
        self.assertEqual((self.case.workspace.root / 'value.txt').read_text(), '2\n')
        events = [json.loads(l) for l in (self.case.store.root / 'events.jsonl').read_text().splitlines()]
        ctx = next(json.loads(e['messages'][1]['content']) for e in events
                   if e['kind'] == 'agent_context' and e['role'] == 'planner' and e['attempt'] == 2)
        self.assertEqual(ctx['progress']['mode'], 'advance')
        self.assertIn('orders.py', ctx['progress']['next_increment'])

    def test_review_cannot_reject_only_for_unfinished_goal(self):
        r = self.review()
        r['accept'] = False
        with self.assertRaisesRegex(ValueError, 'concrete defects'):
            decode_reply('reviewer', {'final': r})
        r.update(defects='api.py returns the wrong status for existing reservations')
        decode_reply('reviewer', {'final': r})
        schema = definitions('reviewer', {'accept': bool}, False)[-1]['function']['parameters']
        self.assertIn('defects', schema['required'])

    def test_green_defective_candidate_still_requires_repair(self):
        response = replies()
        r = self.review()
        r.update(accept=False, defects='value.txt violates an additional documented invariant')
        response[-1] = {'final': r}
        state = self.run_model(response)
        self.assertEqual(state['history'][-1]['outcome'], 'repair_pending')
        self.assertEqual(next_step(state)['mode'], 'repair')
        self.assertEqual(state['accepted_commit'], self.case.initial)

    def test_old_green_candidate_is_reassessed_not_treated_as_failed_checks(self):
        state = self.run_model(replies(accept=False))
        progress = next_step(state)
        self.assertEqual(progress['mode'], 'reassess')
        self.assertTrue(progress['checks_pass'])
        self.assertIn('only missing future features', progress['instruction'])

    def test_failed_verification_overrides_optimistic_structured_review(self):
        response = replies('3\n')
        response[-1] = {'final': self.review(True)}
        state = self.run_model(response)
        self.assertEqual(state['history'][-1]['outcome'], 'repair_pending')
        self.assertEqual(next_step(state)['mode'], 'repair')

    def test_standard_discovery_includes_failing_extra_tests(self):
        root = self.case.workspace.root
        (root / 'tests').mkdir()
        (root / 'tests' / 'test_base.py').write_text('import unittest\nclass Base(unittest.TestCase):\n def test_ok(self): pass\n')
        (root / 'tests_extra').mkdir()
        (root / 'tests_extra' / 'test_orders.py').write_text('import unittest\nclass Orders(unittest.TestCase):\n def test_missing(self): self.fail("orders missing")\n')
        state = self.case.store.read()
        state['checks'] = [[sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v']]
        engine = Engine(self.case.store, ScriptedClient([]), self.case.workspace, emit=lambda _: None)
        results = engine.check(state)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]['returncode'], 0)
        self.assertNotEqual(results[1]['returncode'], 0)
        self.assertIn('orders missing', results[1]['output'])

    def test_noop_is_recorded_without_claiming_progress(self):
        response = [proposal(), {'final': {'approved': True, 'reason': 'Inspect'}},
                    {'tool': 'read_file', 'args': {'path': 'value.txt'}},
                    {'final': {'summary': 'No changes'}}, {'final': self.review()}]
        state = self.run_model(response)
        self.assertFalse(state['history'][-1]['changed_code'])
        self.assertEqual(state['accepted_commit'], self.case.initial)
