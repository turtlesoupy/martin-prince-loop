---
name: martin-prince-loop
description: Follow actual Codex composer predictions as real messages in this chat, using a bounded background loop that waits for each final response. Use for Martin Prince Loop or repeated Codex suggestions, including meta suggestions.
---

# Martin Prince Loop

Sends Codex's own predicted next message into this chat as a real user message, up to five times or 30 minutes. A background worker does the predicting and sending. Your job is to start it, check each message that arrives, and answer.

Repetition, subject changes, and meta suggestions are valid.

## Start

If the invocation message also contains task work, do that first.

```sh
python3 scripts/loop.py start --state /ABSOLUTE/WORKSPACE/work/martin-prince/state.json
```

`scripts/` is relative to this skill. Remember the absolute state path for later turns.

Options, only when the user asks: `--max-iterations N` (default 5), `--max-minutes N` (default 30), `--timeout SECONDS` (default 90, per prediction or queue call), `--retries N` (default 0, extra prediction attempts).

Then end the turn right away. Do not wait, poll the worker, or predict anything yourself. Never reuse or overwrite an existing state file to get more iterations.

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

Do not mention the loop in replies: no counters, state paths, receipts, or announcements of what is queued. For the bare invocation, reply with one short acknowledgment or nothing.

## Stopping and problems

- Stop: `python3 scripts/loop.py stop --state STATE --reason 'WHY'`. A message that was already queued may still arrive; `resume-message` will tell you to answer it.
- Inspect: `python3 scripts/loop.py status --state STATE`. Worker output is in the state file's folder, named `*.worker.log`.
- If `status` shows `dispatching` with a `workerError`, the message may or may not have been queued. Never send it again yourself. If it arrives, run `resume-message` as usual; otherwise tell the user.
- If `status` or the log shows `authChangedInSnapshot` or a warning about `auth.json`, tell the user their Codex login may need renewing.
- If a queued message never arrives, tell the user; their Codex version may not pick up queued messages while idle.
- Do not open another chat or point the loop at a different one.
