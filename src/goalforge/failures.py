"""Failure evidence and test provenance; test failures are observations, not diagnoses."""
import ast
import textwrap
from pathlib import Path
import re

from .context import current_checks
from .state import git


def is_test(path):
    p = Path(path)
    return p.suffix == '.py' and (p.name.startswith('test_') or p.name.endswith('_test.py'))


def original_tests(state):
    return [p for p in git(Path(state['workspace']), 'ls-tree', '-r', '--name-only', '-z',
                           state['start_commit']).split('\0') if p and is_test(p)]


def failure_signature(checks):
    failures = []
    for r in checks:
        if r['returncode'] or r.get('timed_out'):
            names = re.findall(r'^(?:FAIL|ERROR): (.+)$', r.get('output', ''), re.M)
            failures.append((tuple(r['argv']), tuple(names), r.get('timed_out', False)))
    return tuple(failures)


def failure_context(state):
    checks, source = current_checks(state)
    signature = failure_signature(checks)
    matches = [h['attempt'] for h in state['history']
               if signature and failure_signature(h.get('check_results', [])) == signature]
    return {'source': source, 'has_failures': bool(signature),
            'same_failure_attempts': matches[-5:],
            'requirement': 'Observe the actual failure, inspect the test fixture/assertion and implementation, and independently derive the expected result from the goal/contract. Do not treat reviewer hypotheses as verified causes.',
            'repeat_policy': 'If this failure persisted, reproduce or trace state before and after each operation. Do not repeat speculative edits. Classify implementation, test_expectation, environment, or uncertain; uncertainty requires investigation, not weakening tests.'}


def validate_analysis(analyses):
    if not isinstance(analyses, list):
        raise ValueError('failure_analysis must be a list')
    for a in analyses:
        if not isinstance(a, dict) or a.get('classification') not in {'implementation', 'test_expectation', 'environment', 'uncertain'}:
            raise ValueError('Each failure needs a supported classification')
        for key in ('test_file', 'implementation_file', 'observation', 'requirement', 'expected_derivation', 'evidence', 'next_action'):
            if not isinstance(a.get(key), str) or not a[key].strip():
                raise ValueError(f'Failure analysis needs {key}; distinguish observed results from a proposed cause')


def validate_test_corrections(proposal, workspace, policy, inspected):
    corrections = proposal.get('test_corrections', [])
    if not isinstance(corrections, list):
        raise ValueError('test_corrections must be a list')
    seen = set()
    for correction in corrections:
        if not isinstance(correction, dict):
            raise ValueError('Each test correction must be an object')
        path = correction.get('path')
        if not isinstance(path, str):
            raise ValueError('Test correction path must be a string')
        if path in seen:
            raise ValueError('Duplicate test correction')
        seen.add(path)
        if path not in policy.get('generated_tests', []) or workspace.is_protected(path):
            raise ValueError('Only existing agent-generated, unprotected tests can be corrected; original tests stay unchanged')
        old, new = correction.get('old_assertion'), correction.get('new_assertion')
        if not isinstance(old, str) or not old.strip() or not isinstance(new, str) or not new.strip() or old == new:
            raise ValueError('Test correction must specify different nonempty old_assertion and new_assertion')
        validate_assertion_change(old, new)
        source = workspace.path(path).read_text()
        if source.count(old) != 1:
            raise ValueError('old_assertion must match exactly once in the current generated test')
        for key in ('requirement', 'expected_derivation', 'evidence', 'coverage_preserved', 'implementation_file'):
            if not isinstance(correction.get(key), str) or not correction[key].strip():
                raise ValueError(f'Test correction requires {key}; do not merely match the observed output')
        if path not in inspected or correction['implementation_file'] not in inspected:
            raise ValueError('Read the failing test and relevant implementation before proposing a test correction')


def changed_existing_tests(root, base):
    changed = git(root, 'diff', '--cached', '--name-only', '--diff-filter=MD', '-z', base).split('\0')
    return [p for p in changed if p and is_test(p)]


def test_edit_violation(root, base, proposal, original):
    corrections = {c['path']: c for c in proposal.get('test_corrections', [])}
    for path in changed_existing_tests(root, base):
        if path in original:
            return f'Original acceptance test changed: {path}'
        c = corrections.get(path)
        if not c:
            return f'Editing existing generated test {path} requires an evidence-backed test_corrections proposal'
        old = git(root, 'show', f'{base}:{path}', strip=False)
        try:
            new = git(root, 'show', f':{path}', strip=False)
        except RuntimeError:
            return f'Deleting generated test {path} is not a test correction'
        if old.count(c['old_assertion']) != 1 or new != old.replace(c['old_assertion'], c['new_assertion'], 1):
            return f'Test edit in {path} exceeded the exact proposed assertion correction; preserve fixture, operations, and other coverage'
    return None


def validate_assertion_change(old, new):
    """Allow an expected-value correction, never changing the operation under test."""
    try:
        before = ast.parse(textwrap.dedent(old)).body
        after = ast.parse(textwrap.dedent(new)).body
        if len(before) != 1 or len(after) != 1:
            raise ValueError()
        a, b = before[0], after[0]
        if not isinstance(a, ast.Expr) or not isinstance(b, ast.Expr):
            raise ValueError()
        a, b = a.value, b.value
        if not isinstance(a, ast.Call) or not isinstance(b, ast.Call):
            raise ValueError()
        if not isinstance(a.func, ast.Attribute) or a.func.attr not in {'assertEqual', 'assertListEqual', 'assertDictEqual', 'assertTupleEqual', 'assertSetEqual'}:
            raise ValueError()
        if ast.dump(a.func) != ast.dump(b.func) or len(a.args) != 2 or len(b.args) != 2 or a.keywords or b.keywords:
            raise ValueError()
        if ast.dump(a.args[0]) != ast.dump(b.args[0]) or ast.dump(b.args[0]) == ast.dump(b.args[1]):
            raise ValueError()
        # A concrete expected value, not an expression that echoes the observed result.
        ast.literal_eval(b.args[1])
    except (SyntaxError, ValueError, TypeError):
        raise ValueError('Test corrections must keep the same equality assertion and actual expression, changing only its concrete expected value. Do not skip/delete tests or replace checks with tautologies.') from None
