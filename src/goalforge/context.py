"""Small active evidence packets; bounded, read-only retrieval of archived run data."""
import json


def latest_accepted(state):
    return next((h for h in reversed(state['history'])
                 if h.get('outcome') == 'kept' and h.get('review')), {})


def current_checks(state):
    candidate = state.get('candidate')
    if candidate:
        return candidate.get('checks', []), 'candidate'
    accepted = latest_accepted(state)
    if accepted:
        # Never label the initial failing baseline as evidence about accepted code.
        return accepted.get('check_results', []), 'accepted checkpoint'
    return state.get('baseline') or [], 'initial checkpoint (not yet accepted)'


def evidence_packet(state):
    checks, source = current_checks(state)
    return {'source': source, 'checks': [
        {'index': i, 'argv': r['argv'], 'returncode': r['returncode'],
         'timed_out': r.get('timed_out', False),
         **({'failure_tail': r.get('output', '')[-4000:]} if r['returncode'] or r.get('timed_out') else {})}
        for i, r in enumerate(checks)]}


def retrieve_history(store, args, before_attempt):
    """Pagination preserves access to complete evidence without flooding initial prompts."""
    attempt = args.get('attempt')
    section = args.get('section')
    start = args.get('start', 0)
    count = args.get('count', 6000)
    if type(attempt) is not int or not 0 <= attempt < before_attempt:
        raise ValueError('Choose an earlier attempt; 0 refers to the original baseline.')
    if type(start) is not int or start < 0 or type(count) is not int or not 1 <= count <= 12000:
        raise ValueError('start must be nonnegative and count between 1 and 12000')
    state = store.read()
    if attempt == 0 and section == 'checks':
        value = state.get('baseline')
    elif section == 'events':
        value = []
        event_attempt = 0
        path = store.root / 'events.jsonl'
        if path.exists():
            for line in path.read_text().splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event_attempt = event.get('attempt', event_attempt)
                if event_attempt == attempt:
                    value.append(event)
    else:
        entries = [h for h in state['history'] if h.get('attempt') == attempt]
        keys = {'plan': 'proposal', 'review': 'review', 'checks': 'check_results',
                'workers': 'workers', 'outcome': 'lesson'}
        if section not in keys:
            raise ValueError('section must be plan, review, checks, workers, outcome, or events')
        value = [h.get(keys[section]) for h in entries]
    text = json.dumps(store.redact(value), ensure_ascii=False, indent=2)
    return {'attempt': attempt, 'section': section, 'start': start,
            'text': text[start:start + count], 'total_characters': len(text),
            'next_start': start + count if start + count < len(text) else None,
            'warning': 'Historical evidence, not necessarily current. Current evidence takes precedence.'}
