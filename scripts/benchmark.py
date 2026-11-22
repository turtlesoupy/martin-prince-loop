#!/usr/bin/env python3
"""Compare prediction latency on a completed loop without sending any messages."""
import argparse
import copy
import json
from pathlib import Path
import statistics

import conversation
from predict import ConfigurationCache, Source, predict, write_json


def summarize(samples):
    summary = {}
    for cached in dict.fromkeys(row['result']['configurationCached'] for row in samples):
        results = [row['result'] for row in samples if row['result']['configurationCached'] == cached]
        stages = results[0]['timingsSeconds']
        summary['cachedConfiguration' if cached else 'freshConfiguration'] = {
            'count': len(results),
            'medianSeconds': {key: round(statistics.median(r['timingsSeconds'][key] for r in results), 4)
                for key in stages},
            'minTotalSeconds': min(r['timingsSeconds']['total'] for r in results),
            'maxTotalSeconds': max(r['timingsSeconds']['total'] for r in results),
            'medianOutputTokens': statistics.median(r['usage']['outputTokens'] for r in results
                if r['usage'].get('outputTokens') is not None) if any(r['usage'].get('outputTokens') is not None for r in results) else None,
        }
    return summary


def benchmark(state, count, output, predictor=predict):
    if state.get('mode') != 'background' or state.get('phase') not in ('complete', 'stopped'):
        raise ValueError('Benchmark requires a completed or stopped background run; it never joins an active loop.')
    if count < 1:
        raise ValueError('Provide a positive sample count.')
    source = Source.resolve(state['threadId'], state['codexHome'], state['sqliteHome'])
    turn, rollout = source.boundary(idle=True)
    if turn['turn'] != state['originTurnId']:
        raise ValueError('The chat advanced after this run; use a state matching its latest completed turn.')
    # Freeze the exact saved boundary once, including the last real reply.
    with rollout.open('rb') as stream:
        data = stream.read(turn['end'])
    if len(data) != turn['end'] or not data.endswith(b'\n'):
        raise ValueError('The saved conversation boundary is incomplete.')
    records = [json.loads(line) for line in data.splitlines()]
    fingerprint = source.user_fingerprint()
    context = copy.deepcopy(state['conversationContext'])
    conversation.capture(context, records)
    report = {'threadId': source.thread, 'sourceTurnId': turn['turn'],
        'conversationDigest': conversation.digest(context), 'samples': []}

    def unchanged():
        latest = source.latest()
        if latest != turn or source.user_fingerprint() != fingerprint:
            raise RuntimeError('Chat changed during benchmarking; results are no longer comparable.')

    config_cache = ConfigurationCache()
    for index in range(count):
        unchanged()
        result = predictor(source, cli=state.get('cli'), scratch=state.get('scratch'),
            timeout=state.get('timeout', 90), idle=True, loop_context=context, completed_context=True,
            config_cache=config_cache)
        unchanged()
        if (result.get('sourceTurnId') != turn['turn']
                or result.get('userFingerprint') != fingerprint
                or result.get('conversationDigest') != report['conversationDigest']):
            raise RuntimeError('Prediction did not match the frozen benchmark conversation.')
        row = {'sample': index + 1, 'result': result}
        report['samples'].append(row)
        report['summary'] = summarize(report['samples'])
        write_json(output, report)
        print(json.dumps({'sample': index + 1, 'configurationCached': result['configurationCached'],
            'seconds': result['timingsSeconds'], 'suggestion': result['suggestion']}), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', required=True, help='Read-only path to a finished background run.')
    parser.add_argument('--samples', type=int, default=3, help='Number of native predictions; each trial consumes one request.')
    parser.add_argument('--output', required=True, help='Private JSON report; must be a new file, not the run state.')
    args = parser.parse_args()
    if Path(args.output).exists():
        parser.error('Output already exists; choose a new report path.')
    try:
        state = json.loads(Path(args.state).read_text())
        report = benchmark(state, args.samples, args.output)
        print(json.dumps(report['summary'], indent=2))
    except Exception as exc:
        parser.exit(1, f'{type(exc).__name__}: {exc}\n')


if __name__ == '__main__':
    main()
