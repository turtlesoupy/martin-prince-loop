"""Keep conversation work intact while excluding controller execution by boundaries."""
import copy
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


def begin(records, thread, invocation_turn, prompt=None):
    end = turn_start(records, invocation_turn)
    context = {'version': 1, 'threadId': thread, 'segments': [segment(records, 0, end)]}
    prompt_saved = False
    # Preserve runtime updates even when the invocation itself is omitted.
    for i in range(end, len(records)):
        r = records[i]
        p = r.get('payload', {})
        if r.get('type') != 'response_item':
            continue
        if p.get('role') in ('system', 'developer'):
            context['segments'].append(segment(records, i, i + 1))
        elif prompt is not None and not prompt_saved and p.get('role') == 'user' and p.get('type') == 'message':
            for index, part in enumerate(p.get('content', [])):
                text = part.get('text', '')
                # Skill bodies are injected as user messages too. Their examples
                # must never supply a task missing from the actual invocation.
                if text.lstrip().startswith('<skill>'):
                    continue
                if part.get('type') == 'input_text' and prompt in text:
                    # Replace only the invocation text, retaining its original
                    # attachments and other content parts. Never edit the source.
                    context['segments'].append({**segment(records, i, i + 1),
                        'prompt': prompt, 'contentIndex': index})
                    prompt_saved = True
                    break
    if prompt is not None:
        if not prompt_saved:
            raise ValueError('Prompt must be verbatim task text from the invocation message.')
        # Start runs before task work. The controller output is excluded as an
        # orphan; the actual task tools and final answer are captured on completion.
        context['workStart'] = len(records)
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
        if 'prompt' in part:
            selected = copy.deepcopy(selected)
            content = selected[0]['payload']['content'][part['contentIndex']]
            content['text'] = part['prompt']
        kept.extend(selected)
        previous = end
    # The resume controller call starts outside task work, but its output can land
    # after workStart. Remove orphan outputs, not ordinary paired tool activity.
    calls = {r['payload'].get('call_id') for r in kept if r.get('type') == 'response_item'
        and r.get('payload', {}).get('type', '').endswith('_call')}
    return [r for r in kept if not (r.get('type') == 'response_item'
        and r.get('payload', {}).get('type', '').endswith('_call_output')
        and r['payload'].get('call_id') not in calls)]
