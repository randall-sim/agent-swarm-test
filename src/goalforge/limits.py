"""Specific limit stops, separate from the legacy resumable status bucket."""
import re


def limit_stop(kind, limit, used, agent_id=None, role=None):
    labels = {'attempt_limit': 'Attempt limit reached', 'request_limit': 'Shared request limit reached',
              'agent_step_limit': 'Agent step limit reached'}
    if kind == 'attempt_limit':
        message = f'Attempt limit reached: {used}/{limit} attempts in this start/resume session. Resume with more attempts to continue.'
    elif kind == 'request_limit':
        message = f'Shared request limit reached: {used}/{limit} HTTP requests in this start/resume session (all agents and retries). Resume with a new request budget to continue.'
    else:
        message = f'Agent step limit reached: {agent_id} ({role}) used {used}/{limit} model steps in this invocation. Increase its step limit or narrow its task before resuming.'
    return dict(kind=kind, label=labels[kind], limit=limit, used=used,
                agent_id=agent_id, role=role, message=message)


def status_label(state):
    if state.get('status') != 'budget_exhausted':
        return state.get('status', 'unknown')
    stop = state.get('stop_reason')
    if stop:
        return stop['label']
    # Old runs: use explicit recorded messages, never guess from lifetime usage.
    error = state.get('last_error') or ''
    if re.search(r'reached its \d+-step limit', error):
        return 'Agent step limit reached'
    if re.search(r'LLM request budget \(\d+\) exhausted', error):
        return 'Shared request limit reached'
    return 'Limit reached (legacy run; type not recorded)'
