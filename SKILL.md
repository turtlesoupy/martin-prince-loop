---
name: martin-prince-loop
description: Follow actual Codex composer predictions as real messages in this chat, using a bounded background loop that waits for each final response. Use for Martin Prince Loop or repeated Codex suggestions, including meta suggestions.
---

# Martin Prince Loop

Sends Codex's own predicted next message into this chat as a real user message. Defaults to five predicted follow-up turns or 30 minutes, whichever comes first. A background worker does the predicting and sending. Your job is to start it, check each message that arrives, and answer.

Repetition, subject changes, and meta suggestions are valid.

## Start

Accept optional `turns=N` and `timeout=DURATION` in the invocation, or equivalent natural language. For example:

```text
$martin-prince-loop turns=3 timeout=10m draw a sleepy kitten in one line
```

Map these to `--turns 3 --run-timeout 10m` on the start command. Duration accepts `s`, `m`, or `h`; a bare number means seconds. Turns count predicted follow-ups, excluding the supplied initial prompt. The timeout covers the whole run from startup; it stops further messages without interrupting an answer already in progress.

Start once, before doing any supplied task work, and keep this call separate from task tools:

```sh
python3 scripts/loop.py start --state /ABSOLUTE/WORKSPACE/work/martin-prince/state.json
```

`scripts/` is relative to this skill. Remember the absolute state path for later turns.

If the invocation includes a task, pass its verbatim task text as `--prompt TEXT`, excluding the skill invocation and loop options. Preserve file references and task constraints; quote it safely as one argument. After startup, do that task and send its actual answer in final. The worker retains the prompt, attachments, task tools, and answer, then predicts the next message. Do not queue or sample a replacement for the supplied prompt.

For a bare invocation (including one with only loop options), end the turn right away with an empty final response and no acknowledgment text. Do not wait, poll the worker, or predict anything yourself. Never reuse or overwrite an existing state file to get more iterations.

Advanced options, only when requested: `--request-timeout SECONDS` (default 90, per prediction or queue call), `--retries N` (default 0, extra prediction attempts). Existing `--max-iterations`, `--max-minutes`, and per-request `--timeout` command flags remain supported.

## When a predicted message arrives

1. Before anything else, run this once, on its own (not batched with other tool calls):

   ```sh
   python3 scripts/loop.py resume-message --state /ABSOLUTE/WORKSPACE/work/martin-prince/state.json
   ```

2. Read the result:
   - `ready` or `complete`: answer the message normally. On `complete`, nothing more will be queued.
   - `stopped`: the user typed something or the run ended. Follow `instruction` in the output, handle what the user actually said, and do not restart the loop.
   - An error: report it. Do not start a new run to get around it.

3. Reply as you would to any user message, without waiting for anything else.

## Keep it quiet

Do not mention the loop in replies: no counters, state paths, receipts, or announcements of what is queued. Do not add startup acknowledgments such as "Okay," "Started," or "Done."

## Stopping and problems

- Stop: `python3 scripts/loop.py stop --state STATE --reason 'WHY'`. A message that was already queued may still arrive; `resume-message` will tell you to answer it.
- Inspect: `python3 scripts/loop.py status --state STATE`. Worker output is in the state file's folder, named `*.worker.log`.
- If `status` shows `dispatching` with a `workerError`, the message may or may not have been queued. Never send it again yourself. If it arrives, run `resume-message` as usual; otherwise tell the user.
- If `status` or the log shows `authChangedInSnapshot` or a warning about `auth.json`, tell the user their Codex login may need renewing.
- If a queued message never arrives, tell the user; their Codex version may not pick up queued messages while idle.
- Do not open another chat or point the loop at a different one.
