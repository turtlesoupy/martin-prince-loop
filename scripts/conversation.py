"""Keep conversation work intact while excluding controller execution by boundaries."""
import hashlib
import json


def digest(records):
    return hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()


def turn_start(records, turn):
    matches = [i for i, r in enumerate(records) if r.get('type') == 'event_msg'
        and r.get('payload', {}).get('type') == 'task_started'
        and r['payload'].get('turn_id') == turn]
    if not matches:
        raise ValueError('Invocation/delivery turn boundary is missing.')
    return matches[-1]


def segment(records, start, end):
    return {'start': start, 'end': end, 'sha256': digest(records[start:end])}


def begin(records, thread, invocation_turn):
    end = turn_start(records, invocation_turn)
    context = {'version': 1, 'threadId': thread, 'segments': [segment(records, 0, end)]}
    # Preserve runtime updates even when the invocation itself is omitted.
    for i in range(end, len(records)):
        r = records[i]
        if r.get('type') == 'response_item' and r.get('payload', {}).get('role') in ('system', 'developer'):
            context['segments'].append(segment(records, i, i + 1))
    return context


def delivered(context, records, turn):
    """Keep delivered input and runtime updates; subsequent records are task work."""
    project(records, context, context['threadId'])  # Validate saved boundaries first.
    for i in range(turn_start(records, turn), len(records)):
        r = records[i]
        p = r.get('payload', {})
        if r.get('type') == 'response_item' and p.get('type') == 'message' and p.get('role') in ('user', 'system', 'developer'):
            context['segments'].append(segment(records, i, i + 1))
    context['workStart'] = len(records)


def capture(context, records):
    """End task work before prediction/queue administration starts."""
    project(records, context, context['threadId'])
    start = context.pop('workStart', None)
    if start is not None and start < len(records):
        context['segments'].append(segment(records, start, len(records)))


def project(records, context, thread):
    if context.get('version') != 1 or context.get('threadId') != thread:
        raise ValueError('Conversation context belongs to a different thread or version.')
    kept, previous = [], 0
    for part in context['segments']:
        start, end = part['start'], part['end']
        if not (isinstance(start, int) and isinstance(end, int) and previous <= start <= end <= len(records)):
            raise ValueError('Conversation boundaries are invalid or reordered.')
        selected = records[start:end]
        if digest(selected) != part['sha256']:
            raise ValueError('Conversation source changed; do not silently rebase.')
        kept.extend(selected)
        previous = end
    # The resume controller call starts outside task work, but its output can land
    # after workStart. Remove orphan outputs, not ordinary paired tool activity.
    calls = {r['payload'].get('call_id') for r in kept if r.get('type') == 'response_item'
        and r.get('payload', {}).get('type', '').endswith('_call')}
    return [r for r in kept if not (r.get('type') == 'response_item'
        and r.get('payload', {}).get('type', '').endswith('_call_output')
        and r['payload'].get('call_id') not in calls)]
