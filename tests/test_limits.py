"""Stop causes are independent of lifetime counters and remain resumable."""
import unittest
import test_goalforge as fixtures
from goalforge.engine import Engine
from goalforge.limits import status_label


class LimitTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.EngineTests()
        self.case.setUp()
        self.addCleanup(self.case.tearDown)

    def run_engine(self, replies, limit=1000, **kwargs):
        return Engine(self.case.store, fixtures.ScriptedClient(replies, limit),
                      self.case.workspace, emit=lambda _: None, **kwargs).execute(1)

    def test_attempt_limit_is_not_request_exhaustion(self):
        state = self.run_engine(fixtures.replies(complete=False))
        self.assertEqual(state['stop_reason']['kind'], 'attempt_limit')
        self.assertEqual(state['stop_reason']['limit'], 1)
        self.assertLess(state['usage']['requests'], 1000)
        self.assertEqual(status_label(state), 'Attempt limit reached')

    def test_shared_request_limit_and_resume_clears_reason(self):
        state = self.run_engine([fixtures.proposal()], limit=1)
        self.assertEqual(state['stop_reason']['kind'], 'request_limit')
        self.assertEqual(state['stop_reason']['used'], 1)
        self.assertEqual(state['stop_reason']['limit'], 1)
        state = self.run_engine(fixtures.replies())
        self.assertEqual(state['status'], 'complete')
        self.assertNotIn('stop_reason', state)

    def test_coder_limit_identifies_agent_and_role(self):
        responses = [fixtures.proposal(), {'final': {'approved': True, 'reason': 'Okay'}},
                     {'tool': 'read_file', 'args': {'path': 'value.txt'}}]
        state = self.run_engine(responses, coder_steps=1)
        stop = state['stop_reason']
        self.assertEqual(stop['kind'], 'agent_step_limit')
        self.assertEqual((stop['agent_id'], stop['role'], stop['limit']), ('coder', 'coder', 1))
        self.assertIn('Agent step limit reached', state['last_error'])

    def test_legacy_status_does_not_guess_from_usage(self):
        self.assertIn('not recorded', status_label({'status': 'budget_exhausted', 'usage': {'requests': 1000}}))
        self.assertEqual(status_label({'status': 'budget_exhausted', 'last_error': 'coder reached its 12-step limit'}), 'Agent step limit reached')
        self.assertEqual(status_label({'status': 'budget_exhausted', 'last_error': 'LLM request budget (80) exhausted.'}), 'Shared request limit reached')
