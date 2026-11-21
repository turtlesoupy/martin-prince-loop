#!/usr/bin/env python3
"""Build a private prediction context from exact, explicitly selected task messages."""
import argparse
import hashlib
import json
from pathlib import Path
from predict import Source, task_projection, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--thread')
    parser.add_argument('--output', required=True)
    parser.add_argument('--indices', type=int, nargs='+', required=True,
                        help='Zero-based source record positions, in conversation order.')
    parser.add_argument('--append', action='store_true')
    args = parser.parse_args()
    source = Source.resolve(args.thread)
    records = source.records()
    path = Path(args.output)
    if path.exists() and not args.append:
        raise ValueError('Context exists; use --append for new task messages.')
    context = json.loads(path.read_text()) if args.append else {
        'version': 1, 'threadId': source.thread, 'messages': []}
    if args.append:
        task_projection(records, context, source.thread)
    for index in args.indices:
        if index < 0 or index >= len(records):
            raise ValueError('Source record index out of range.')
        context['messages'].append({'index': index, 'sha256': hashlib.sha256(
            json.dumps(records[index], sort_keys=True).encode()).hexdigest()})
    task_projection(records, context, source.thread)
    write_json(path, context)
    print(json.dumps({'messages': len(context['messages']), 'path': str(path.resolve())}))


if __name__ == '__main__':
    main()
