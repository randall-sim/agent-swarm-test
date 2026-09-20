import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from goalforge.engine import Engine, REQUIRED
from goalforge.provider import Budget, Client
from goalforge.state import Store
from goalforge.tool_schemas import definitions
from goalforge.workspace import Workspace


def response(name, args, call_id):
    return io.BytesIO(json.dumps({"choices": [{"message": {"role": "assistant", "content": None,
                      "tool_calls": [{"id": call_id, "type": "function",
                                      "function": {"name": name, "arguments": json.dumps(args)}}]}}]}).encode())


class NativeToolTests(unittest.TestCase):
    def test_native_read_write_finish_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'demo.py').write_text('VALUE = 0\n')
            client = Client('https://api.openai.com/v1', 'gpt-5.4-mini', 'fake', Budget(10))
            replies = [response('read_file', {'path': 'demo.py'}, 'call-read'),
                       response('write_file', {'path': 'demo.py', 'content': 'VALUE = 1\n'}, 'call-write'),
                       response('finish', {'summary': 'Implemented'}, 'call-finish')]
            with patch.object(client.opener, 'open', side_effect=replies) as mocked:
                result = Engine(Store(root), client, Workspace(root, lambda _: False),
                                emit=lambda _: None).role('coder', {'goal': 'Set value to one'})
            self.assertEqual(result, {'summary': 'Implemented'})
            self.assertEqual((root / 'demo.py').read_text(), 'VALUE = 1\n')
            payloads = [json.loads(call.args[0].data) for call in mocked.call_args_list]
            self.assertEqual(payloads[0]['tool_choice'], 'required')
            self.assertFalse(payloads[0]['parallel_tool_calls'])
            self.assertIn('write_file', [tool['function']['name'] for tool in payloads[0]['tools']])
            for payload, expected_id in zip(payloads[1:], ['call-read', 'call-write']):
                self.assertEqual(payload['messages'][-2]['role'], 'assistant')
                self.assertEqual(payload['messages'][-2]['tool_calls'][0]['id'], expected_id)
                self.assertEqual(payload['messages'][-1]['role'], 'tool')
                self.assertEqual(payload['messages'][-1]['tool_call_id'], expected_id)

    def test_premature_finish_gets_native_tool_feedback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = Client('https://api.openai.com/v1', 'gpt-5.4-mini', 'fake', Budget(10))
            replies = [response('finish', {'summary': 'No tools available'}, 'call-too-soon'),
                       response('list_files', {}, 'call-list'),
                       response('finish', {'summary': 'Inspected workspace'}, 'call-done')]
            with patch.object(client.opener, 'open', side_effect=replies) as mocked:
                result = Engine(Store(root), client, Workspace(root, lambda _: False), emit=lambda _: None).role('coder', {})
            correction = json.loads(mocked.call_args_list[1].args[0].data)['messages'][-1]
            self.assertEqual(correction['role'], 'tool')
            self.assertEqual(correction['tool_call_id'], 'call-too-soon')
            self.assertIn('No workspace tools have been called', correction['content'])
            self.assertEqual(result['summary'], 'Inspected workspace')

    def test_readonly_roles_have_no_native_write_or_command_tools(self):
        for role in ('planner', 'critic', 'reviewer'):
            tools = definitions(role, REQUIRED[role], parallel=True)
            names = {tool['function']['name'] for tool in tools}
            self.assertEqual(names, {'list_files', 'read_file', 'search', 'retrieve_history', 'finish'})
        finish = definitions('planner', REQUIRED['planner'], True)[-1]
        self.assertIn('tasks', finish['function']['parameters']['required'])

    def test_compatible_provider_keeps_text_protocol(self):
        client = Client('https://example.com/v1', 'test', 'fake', Budget(1))
        result = {'final': {'approved': True, 'reason': 'OK'}}
        payload = io.BytesIO(json.dumps({'choices': [{'message': {'content': json.dumps(result)}}]}).encode())
        with patch.object(client.opener, 'open', return_value=payload) as mocked:
            self.assertEqual(client.complete([], tools=definitions('critic', REQUIRED['critic'], False)), result)
        body = json.loads(mocked.call_args.args[0].data)
        self.assertNotIn('tools', body)
        self.assertIn('max_tokens', body)
