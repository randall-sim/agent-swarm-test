"""A generated test can be wrong without authorizing acceptance-test weakening."""
import os
import py_compile
import sys
import unittest

import test_goalforge as fixtures
from goalforge.engine import Engine
from goalforge.failures import (failure_context, validate_assertion_change,
                                validate_test_corrections, test_edit_violation)
from goalforge.state import git
from goalforge.workspace import run_process


class FailureDiagnosisTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.EngineTests()
        self.case.setUp()
        self.addCleanup(self.case.tearDown)
        self.engine = Engine(self.case.store, fixtures.ScriptedClient([]), self.case.workspace, emit=lambda _: None)
        self.path = 'tests_extra/test_stock.py'
        self.old = 'self.assertEqual(remaining(), 3)'
        self.new = 'self.assertEqual(remaining(), 4)'
        self.correction = dict(path=self.path, old_assertion=self.old, new_assertion=self.new,
                               requirement='Replay and reopen must not consume stock',
                               expected_derivation='5 available - 0 replay - 1 new order - 0 reopen = 4',
                               evidence='The unchanged operations consume exactly one unit',
                               coverage_preserved='Same equality check and fixture operations', implementation_file='stock.py')

    def model(self, responses):
        return Engine(self.case.store, fixtures.ScriptedClient(responses), self.case.workspace,
                      emit=lambda _: None).execute(1)

    def test_generated_expectation_can_be_corrected_and_independently_reviewed(self):
        state = self.case.store.read()
        state['checks'].append([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests_extra', '-v'])
        self.case.store.write(state)
        test = 'import unittest\nfrom stock import remaining\nclass StockTests(unittest.TestCase):\n def test_balance(self):\n  ' + self.old + '\n'
        initial = fixtures.replies(accept=False)
        initial[3:3] = [
            {'tool': 'write_file', 'args': {'path': 'stock.py', 'content': 'def remaining():\n return 5 - 1\n'}},
            {'tool': 'write_file', 'args': {'path': self.path, 'content': test}},
        ]
        state = self.model(initial)
        self.assertEqual(state['history'][-1]['outcome'], 'repair_pending')
        candidate = state['candidate']['commit']
        plan = fixtures.proposal('Correct generated stock expectation')
        plan['final']['test_corrections'] = [self.correction]
        read_test = {'tool': 'read_file', 'args': {'path': self.path}}
        read_code = {'tool': 'read_file', 'args': {'path': 'stock.py'}}
        review = dict(accept=True, complete=True, reason='Independently derived 4', lesson='Preserved invariant',
                      test_corrections_valid=True, test_correction_review='5 - 0 - 1 - 0 = 4; same operations and stock comparison')
        response = [read_test, read_code, plan,
                    {'final': {'approved': True, 'reason': 'Evidence-backed correction'}},
                    {'tool': 'replace_text', 'args': {'path': self.path, 'old': self.old, 'new': self.new}},
                    {'final': {'summary': 'Corrected expected stock; implementation unchanged'}},
                    read_test, read_code, {'final': review}]
        state = self.model(response)
        self.assertEqual(state['status'], 'complete', state['history'][-1])
        self.assertEqual(git(self.case.workspace.root, 'diff', '--name-only', candidate, state['accepted_commit']), self.path)
        self.assertTrue(all(r['returncode'] == 0 for r in state['history'][-1]['check_results']))
        self.assertEqual(state['history'][-1]['review']['test_correction_review'], review['test_correction_review'])

    def test_test_correction_requires_provenance_and_inspection(self):
        root = self.case.workspace.root
        (root / 'tests_extra').mkdir()
        (root / self.path).write_text(self.old)
        proposal = {'test_corrections': [self.correction]}
        with self.assertRaisesRegex(ValueError, 'agent-generated'):
            validate_test_corrections(proposal, self.case.workspace, {'generated_tests': []}, {self.path, 'stock.py'})
        with self.assertRaisesRegex(ValueError, 'Read the failing test'):
            validate_test_corrections(proposal, self.case.workspace, {'generated_tests': [self.path]}, set())
        validate_test_corrections(proposal, self.case.workspace, {'generated_tests': [self.path]}, {self.path, 'stock.py'})

    def test_cannot_skip_or_echo_actual_result_as_expected(self):
        for new in ['pass', 'self.assertTrue(True)', 'self.assertEqual(remaining(), remaining())',
                    'self.assertEqual(4, 4)', 'self.assertEqual(remaining(), 4)\nself.skipTest("skip")']:
            with self.subTest(new=new), self.assertRaises(ValueError):
                validate_assertion_change(self.old, new)
        validate_assertion_change(self.old, self.new)

    def test_staged_test_edits_cannot_exceed_proposal_or_change_originals(self):
        root = self.case.workspace.root
        (root / 'tests_extra').mkdir()
        (root / self.path).write_text(self.old + '\n')
        git(root, 'add', '.')
        git(root, '-c', 'user.name=Test', '-c', 'user.email=test@localhost', 'commit', '-m', 'generated test fixture')
        base = git(root, 'rev-parse', 'HEAD')
        (root / self.path).write_text(self.new + '\n')
        git(root, 'add', '.')
        self.assertIn('requires', test_edit_violation(root, base, {}, []))
        proposal = {'test_corrections': [self.correction]}
        self.assertIn('Original', test_edit_violation(root, base, proposal, [self.path]))
        self.assertIsNone(test_edit_violation(root, base, proposal, []))
        (root / self.path).write_text(self.new + '\n# unrelated change\n')
        git(root, 'add', '.')
        self.assertIn('exceeded', test_edit_violation(root, base, proposal, []))

    def test_repeated_failure_is_observation_not_proof_of_cause(self):
        check = dict(argv=['python', '-m', 'unittest'], returncode=1, timed_out=False,
                     output='FAIL: test_legacy (Orders)\n4 != 3\nRan in 0.1s')
        state = self.case.store.read()
        state.update(candidate={'checks': [check]}, history=[{'attempt': 1, 'check_results': [check]}, {'attempt': 2, 'check_results': [{**check, 'output': check['output'].replace('0.1s','0.2s')}]}])
        diagnosis = failure_context(state)
        self.assertEqual(diagnosis['same_failure_attempts'], [1, 2])
        self.assertIn('hypotheses', diagnosis['requirement'])

    def test_verification_ignores_same_size_stale_python_bytecode(self):
        root = self.case.workspace.root
        module = root / 'cached_value.py'
        module.write_text('VALUE = 3\n')
        stamp = module.stat().st_mtime
        py_compile.compile(str(module), doraise=True)
        module.write_text('VALUE = 4\n')
        os.utime(module, (stamp, stamp))
        result = run_process([sys.executable, '-c', 'import cached_value; assert cached_value.VALUE == 4'],
                             root, 10, fresh_python_cache=True)
        self.assertEqual(result['returncode'], 0, result['output'])

    def test_failure_diagnosis_requires_reading_both_sides(self):
        analysis = dict(classification='test_expectation', test_file=self.path, implementation_file='stock.py',
                        observation='4 != 3', requirement='Preserve stock', expected_derivation='5-1=4', evidence='Fixture operations', next_action='Correct assertion')
        with self.assertRaisesRegex(ValueError, 'Read both'):
            self.engine.validate_decision('planner', {'failure_analysis': [analysis]}, {'failure_diagnosis': {'has_failures': True}}, set())
        self.engine.validate_decision('planner', {'failure_analysis': [analysis]}, {'failure_diagnosis': {'has_failures': True}}, {self.path, 'stock.py'})
        context = {'proposal': {'test_corrections': [self.correction]}}
        with self.assertRaisesRegex(ValueError, 'Cannot accept'):
            self.engine.validate_decision('reviewer', dict(accept=True, test_corrections_valid=False, test_correction_review='Unproven'), context, {self.path, 'stock.py'})
