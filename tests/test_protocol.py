import json
from pathlib import Path
import tempfile
import unittest

from goalforge.engine import Engine, decode_reply
from goalforge.provider import Budget, ProviderError
from goalforge.state import Store
from goalforge.workspace import Workspace


class Replies:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.budget = Budget(12)
        self.messages = []

    def complete(self, messages, **kwargs):
        self.budget.reserve()
        self.messages.append(list(messages))
        value = next(self.replies)
        if isinstance(value, Exception):
            raise value
        return value


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.output = []
        self.store = Store(self.root)
        self.workspace = Workspace(self.root, lambda _: False)

    def tearDown(self):
        self.tmp.cleanup()

    def engine(self, replies, workers=1):
        return Engine(self.store, Replies(replies), self.workspace, workers=workers, emit=self.output.append)

    def test_real_failed_plan_is_accepted_in_one_call(self):
        fixture = Path(__file__).parent / 'fixtures' / 'planner_unwrapped.json'
        plan = json.loads(fixture.read_text())
        engine = self.engine([plan], workers=3)
        result = engine.role('planner', {'attempt': 1, 'max_workers': 3})
        self.assertEqual(result, plan)
        self.assertEqual(engine.client.budget.used, 1)
        events = [json.loads(line) for line in (self.root / 'events.jsonl').read_text().splitlines()]
        self.assertTrue(any(e['kind'] == 'response_normalized' for e in events))

    def test_wrapped_and_unwrapped_answers_for_each_role(self):
        answers = {'planner': {'title': 'Fix', 'approach': 'Inspect', 'acceptance': 'Checks pass'},
                   'critic': {'approved': True, 'reason': 'Sound'},
                   'coder': {'summary': 'Implemented'},
                   'reviewer': {'accept': False, 'complete': False, 'reason': 'Incomplete', 'lesson': 'Add checks'}}
        for name, answer in answers.items():
            for reply in (answer, {'final': answer}):
                with self.subTest(role=name, wrapped='final' in reply):
                    self.assertEqual(decode_reply(name, reply), ('final', answer))

    def test_invalid_boolean_and_ambiguous_action_still_rejected(self):
        for reply in ({'approved': 'false', 'reason': 'No'},
                      {'tool': 'list_files', 'final': {'approved': True, 'reason': 'OK'}},
                      {'tool': 'list_files', 'approved': True, 'reason': 'OK'}):
            with self.assertRaises(ValueError):
                decode_reply('critic', reply)

    def test_missing_fields_get_actionable_feedback_and_stop_after_three(self):
        engine = self.engine([{'approved': 'yes'}] * 12)
        with self.assertRaisesRegex(ProviderError, '3 consecutive invalid'):
            engine.role('critic', {})
        self.assertEqual(engine.client.budget.used, 3)
        correction = engine.client.messages[1][-1]['content']
        self.assertIn('approved must be bool', correction)
        self.assertIn('"final"', correction)
        self.assertTrue(any('Invalid response 1/3' in line for line in self.output))

    def test_malformed_json_stops_after_three(self):
        error = ProviderError('LLM response was not the expected JSON object.')
        engine = self.engine([error] * 12)
        with self.assertRaisesRegex(ProviderError, '3 consecutive invalid'):
            engine.role('planner', {})
        self.assertEqual(engine.client.budget.used, 3)

    def test_unwrapped_plan_still_requires_valid_ownership(self):
        plan = json.loads((Path(__file__).parent / 'fixtures' / 'planner_unwrapped.json').read_text())
        plan['tasks'][1]['files'] = plan['tasks'][0]['files']
        engine = self.engine([plan] * 12, workers=3)
        with self.assertRaisesRegex(ProviderError, 'Overlapping file ownership'):
            engine.role('planner', {})
        self.assertEqual(engine.client.budget.used, 3)

    def test_valid_tool_resets_invalid_response_streak(self):
        engine = self.engine([{}, {}, {'tool': 'list_files', 'args': {}}, {}, {}, {'summary': 'Done'}])
        self.assertEqual(engine.role('coder', {}), {'summary': 'Done'})
        self.assertEqual(engine.client.budget.used, 6)
