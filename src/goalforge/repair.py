"""Durable, unaccepted candidate checkpoints, separate from the accepted run branch."""
from pathlib import Path

from .state import git, rollback


def restore_candidate(state):
    """Keep HEAD on accepted code while loading the candidate tree into the index/worktree."""
    rollback(state)
    candidate = state.get('candidate')
    if candidate:
        if candidate['accepted_base'] != state['accepted_commit']:
            raise RuntimeError('Repair candidate does not match the accepted checkpoint; discard it explicitly before resuming.')
        git(Path(state['workspace']), 'read-tree', '--reset', '-u', candidate['commit'])


def checkpoint_candidate(store, state, checks, implementation):
    root = Path(state['workspace'])
    old = state.get('candidate') or {}
    tree = git(root, 'write-tree')
    unchanged = old.get('tree') == tree
    commit = old.get('commit') if unchanged else git(
        root, '-c', 'user.name=GoalForge', '-c', 'user.email=goalforge@localhost',
        'commit-tree', tree, '-p', state['accepted_commit'], '-m',
        f"Unaccepted GoalForge candidate: attempt {state['attempt']}")
    # A private ref keeps the candidate reachable without moving the accepted branch.
    ref = f"refs/goalforge/candidates/{state['id']}/attempt-{state['attempt']}"
    git(root, 'update-ref', ref, commit)
    candidate = dict(commit=commit, tree=tree, ref=ref, accepted_base=state['accepted_commit'],
                     attempt=state['attempt'], rounds=old.get('rounds', 0) + 1,
                     unchanged_rounds=old.get('unchanged_rounds', 0) + 1 if unchanged else 0,
                     checks=checks, implementation=implementation,
                     review=None, review_status='pending')
    state['candidate'] = store.redact(candidate)
    store.write(state)  # Durable even if the reviewer call hits a budget or API limit.
    store.event('candidate_saved', attempt=state['attempt'], commit=commit,
                unchanged=unchanged, repair_round=candidate['rounds'])
    return state['candidate']


def clear_candidate(store, state, reason):
    candidate = state.pop('candidate', None)
    store.write(state)
    # Historical candidate commits remain referenced for inspection/recovery.
    # The pointer in state, not ref existence, defines the active candidate.
    if candidate:
        store.event('candidate_cleared', attempt=state['attempt'], reason=reason,
                    commit=candidate['commit'])
