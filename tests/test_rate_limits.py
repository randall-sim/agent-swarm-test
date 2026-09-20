import io
import json
import unittest
from email.message import Message
from unittest.mock import patch
from urllib.error import HTTPError

from goalforge.provider import Budget, BudgetExceeded, Client, ProviderError, RateGate, RateLimitExceeded, retry_delay


class Clock:
    def __init__(self):
        self.now = 0.0

    def sleep(self, seconds):
        self.now += seconds


def http_error(code=429, headers=None, body=None):
    message = Message()
    for name, value in (headers or {}).items():
        message[name] = value
    return HTTPError('https://example.com', code, 'error', message,
                     io.BytesIO(json.dumps(body or {}).encode()))


def success():
    return io.BytesIO(json.dumps({'choices': [{'message': {'content': '{"ok": true}'}}]}).encode())


class RateLimitTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.time = patch('goalforge.provider.time.monotonic', side_effect=lambda: self.clock.now)
        self.sleep = patch('goalforge.provider.time.sleep', side_effect=self.clock.sleep)
        self.time.start()
        self.sleep.start()
        self.addCleanup(self.time.stop)
        self.addCleanup(self.sleep.stop)
        self.client = Client('http://localhost/v1', 'test', 'secret', Budget(20))
        self.events = []
        self.client.on_event = self.events.append

    def test_retry_after_preserves_payload_and_counts_attempts(self):
        with patch.object(self.client.opener, 'open', side_effect=[http_error(headers={'Retry-After': '12'}), success()]) as send:
            self.assertEqual(self.client.complete([{'role': 'user', 'content': 'goal'}]), {'ok': True})
        self.assertGreaterEqual(self.clock.now, 12)
        self.assertEqual(self.client.budget.used, 2)
        self.assertEqual(send.call_args_list[0].args[0].data, send.call_args_list[1].args[0].data)
        self.assertTrue(any(e['kind'] == 'provider_retry' for e in self.events))

    def test_quota_is_not_retried_or_echoed(self):
        for code in ('insufficient_quota', 'credit_balance_exhausted', 'project_spend_limit_exceeded'):
            with patch.object(self.client.opener, 'open', side_effect=http_error(body={'error': {'code': code, 'message': 'secret'}})) as send:
                with self.assertRaisesRegex(ProviderError, 'quota or billing') as caught:
                    self.client.complete([])
                self.assertNotIn('secret', str(caught.exception))
                self.assertEqual(send.call_count, 1)

    def test_long_header_never_retries_early(self):
        with patch.object(self.client.opener, 'open', side_effect=http_error(headers={'Retry-After': '900'})) as send:
            with self.assertRaises(RateLimitExceeded):
                self.client.complete([])
            self.assertEqual(send.call_count, 1)

    def test_retries_are_bounded(self):
        with patch.object(self.client.opener, 'open', side_effect=http_error(headers={'Retry-After': '0'})) as send:
            with self.assertRaises(RateLimitExceeded):
                self.client.complete([])
            self.assertEqual(send.call_count, 7)

    def test_shared_gate_waits_and_staggers_forks(self):
        child = self.client.fork()
        self.assertIs(child.rate_gate, self.client.rate_gate)
        self.client.rate_gate.defer(20)
        with patch.object(child.opener, 'open', return_value=success()):
            child.complete([])
        self.assertGreaterEqual(self.clock.now, 20)
        with patch.object(self.client.opener, 'open', return_value=success()):
            self.client.complete([])
        self.assertGreaterEqual(self.clock.now, 20.5)
        self.assertEqual(self.client.budget.used, 2)

    def test_wait_is_cancellable_before_request(self):
        self.client.rate_gate.defer(20)
        def cancel():
            if self.clock.now >= .5:
                raise RuntimeError('paused')
        self.client.check_cancelled = cancel
        with patch.object(self.client.opener, 'open') as send:
            with self.assertRaisesRegex(RuntimeError, 'paused'):
                self.client.complete([])
            send.assert_not_called()
        self.assertLessEqual(self.clock.now, .5)

    def test_budget_limit_does_not_wait_or_send(self):
        self.client.budget.used = 20
        self.client.rate_gate.defer(50)
        with self.assertRaises(BudgetExceeded):
            self.client.complete([])
        self.assertEqual(self.clock.now, 0)

    def test_header_formats_and_fallback(self):
        self.assertEqual(retry_delay({'retry-after-ms': '1500'}, 0), 1.5)
        self.assertEqual(retry_delay({'x-ratelimit-remaining-tokens': '0', 'x-ratelimit-reset-tokens': '1m2s500ms'}, 0), 62.5)
        with patch('goalforge.provider.time.time', return_value=0):
            self.assertEqual(retry_delay({'retry-after': 'Thu, 01 Jan 1970 00:01:00 GMT'}, 0), 60)
        with patch('goalforge.provider.random.uniform', return_value=.5):
            self.assertEqual(retry_delay({'retry-after': 'invalid'}, 2), 20.5)
            self.assertEqual(retry_delay({'retry-after': 'nan'}, 0), 5.5)

    def test_service_overload_honors_header(self):
        with patch.object(self.client.opener, 'open', side_effect=[http_error(503, {'Retry-After': '3'}), success()]):
            self.client.complete([])
        self.assertGreaterEqual(self.clock.now, 3)
