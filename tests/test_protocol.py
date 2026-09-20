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
        self.schemas = []

    def complete(self, messages, **kwargs):
        self.budget.reserve()
        self.messages.append(list(messages))
        self.schemas.append(kwargs.get("tools"))
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

    def test_context_snapshots_match_model_input_and_redact_secret(self):
        self.store.secret = 'private-example-key'
        engine = self.engine([
            {'tool': 'read_file', 'args': {'path': 'contract.txt'}},
            {'approved': True, 'reason': 'Contract matches'},
        ])
        (self.root / 'contract.txt').write_text('Shared shape: id, title')
        context = {'attempt': 2, 'goal': 'private-example-key',
                   'proposal': {'approach': 'Share id and title'},
                   'recent_history': [{'attempt': 1, 'lesson': 'Fix shape mismatch'}]}
        engine.role('critic', context)
        raw = (self.root / 'events.jsonl').read_text()
        self.assertNotIn('private-example-key', raw)
        snapshots = [e for e in map(json.loads, raw.splitlines()) if e['kind'] == 'agent_context']
        self.assertEqual(len(snapshots), 2)
        for index, event in enumerate(snapshots):
            self.assertEqual(event['messages'], self.store.redact(engine.client.messages[index]))
            self.assertEqual(event['attempt'], 2)
            self.assertEqual(event['request_number'], index + 1)
            self.assertEqual(event['run_id'], self.root.name)
        self.assertIn('Planner', snapshots[0]['sources']['proposal'])
        self.assertIn('Shared shape', snapshots[1]['messages'][-1]['content'])
        self.assertIn('Fix shape mismatch', snapshots[1]['messages'][1]['content'])


    def test_deadlock_fallback_enforces_single_task_in_schema_and_validation(self):
        plan = json.loads((Path(__file__).parent / 'fixtures' / 'planner_unwrapped.json').read_text())
        single = {**plan, 'tasks': [plan['tasks'][0]]}
        engine = self.engine([plan, single], workers=3)
        result = engine.role('planner', {'attempt': 3, 'max_workers': 1,
                                        'planning': {'max_workers': 1, 'blockers': []}})
        self.assertEqual(len(result['tasks']), 1)
        self.assertEqual(engine.client.budget.used, 2)
        self.assertEqual(engine.client.schemas[0][-1]['function']['parameters']['properties']['tasks']['maxItems'], 1)
        self.assertIn('between 1 and 1', engine.client.messages[1][-1]['content'])
        self.assertIn('DEFERRED WORK', engine.client.messages[0][0]['content'])
