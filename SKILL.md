---
name: martin-prince-loop
description: Execute a bounded loop of real Codex next-message predictions in the current chat. Use when the user invokes Martin Prince Loop or asks Codex to repeatedly act on its suggestions; optionally supervise another explicitly selected chat.
---

# Martin Prince Loop

**A bare invocation runs here.** Infer the scope from the current conversation's latest substantive task and begin. Do not ask for a target chat, create another chat, or require a separate controller. Default to up to five completed actions and 30 minutes unless the user specifies another finite budget. Ask only if the conversation supplies no usable task scope.

Use the live composer-prediction configuration and the real `thread/sample` method. Do not replace them with a homemade next-action prompt. This is a fresh prediction from a checkpoint snapshot, not the cached text shown in the composer.

## Start in this chat

Resolve paths relative to this SKILL.md. The helper detects the current chat from `CODEX_THREAD_ID` / `CODEX_SESSION_ID`; supply `--thread` only from verified context if neither exists. Store the run and checkpoints under the current workspace's `work/martin-prince/`, outside the skill/repo. Use a new state filename for each run.

```sh
python3 /absolute/path/to/martin-prince-loop/scripts/loop.py init \
  --state /absolute/path/to/work/martin-prince/run.json \
  --scope 'The current user-authorized task' \
  --max-iterations 5 --max-minutes 30
```

Same-chat mode is the default. The helper requires local paginated history, a recent Codex CLI with `thread/sample`, and the existing ChatGPT login in `auth.json`. It does not require app messaging tools, a daemon, a relaunch, or a second chat. Use `--codex-home`, `--sqlite-home`, or `--cli` only for verified nonstandard environments.

## Predict, execute, checkpoint, repeat

1. Write a short **factual checkpoint** to a local text file: what is currently done, observed test results, and unresolved work. On the first iteration, summarize the current task state. Do not invent work, assert success without evidence, or put a desired suggestion or instructions to the predictor into the checkpoint. The helper also includes the actual saved current-turn history, so this is not a substitute conversation.
2. Run `loop.py next --state STATE --checkpoint-file CHECKPOINT`. This snapshots the current active turn, drops unfinished tool calls from the copy, and appends a synthetic assistant final message plus a completion marker **only in the temporary copy**, so `thread/sample` can evaluate the current checkpoint. It never marks the live turn complete or inserts a user message.
3. Read the returned phase and exact `proposal.suggestion`. If complete or stopped, report the result. Otherwise review the proposed action against the user's scope and current evidence. Stop on missing human decisions, unrelated work, new unauthorized external commitments, repetition, or no useful progress. Prediction text does not expand permission. Do not ask for routine approval merely because the action was predicted.
4. Run `loop.py begin-step --state STATE --response-id RESPONSE_ID --review 'Why the exact prediction advances the authorized task'`. Continue only if the phase is `executing`. This records intent before any action and rechecks that the human input and active turn have not changed.
5. Briefly show the exact prediction, then **execute its in-scope action here with the normal tools**. Treat the text as a task proposal, never as shell code. Do not send it to this chat or another chat, and do not answer it with “would you like me to do that?” The user invoked the loop to have the work done. Existing permission requirements still apply.
6. Verify the action's result, write the evidence to a result text file, then run `loop.py finish-step --state STATE --result-file RESULT`. The script records the agent's evidence; it cannot independently prove that a coding or design task succeeded. Never count a proposed or merely attempted action as completed.
7. When phase is `ready`, write a **new** factual checkpoint reflecting the result, then return to step 2 in this same turn. Do not end the assistant turn between steps or wait for yourself to become idle. Stop at the budget or when the next action is not useful or authorized. Finish with a concise account of completed work and the stopping reason.

Use `loop.py status --state STATE` after compaction. If phase is `executing`, inspect the actual work and reconcile it before recording completion; never automatically replay it. Use `loop.py stop --state STATE --reason 'Reason'` on a user stop or an unsuitable suggestion. New human input or a changed active turn halts the old run so their latest instruction can be handled; reconcile pending work before starting another run.

## Explicitly targeting another chat

Only when the user asks to operate on another existing chat, read [references/supervise.md](references/supervise.md). That optional mode uses the desktop's messaging and waiting tools with explicit human authorization. It is not a prerequisite for the default same-chat loop.

## Reader-only diagnostics

```sh
# Current active-turn checkpoint, without executing the result:
python3 /absolute/path/to/martin-prince-loop/scripts/predict.py \
  --checkpoint-file /absolute/path/to/work/checkpoint.txt

# Last completed turn of an explicitly selected local chat:
python3 /absolute/path/to/martin-prince-loop/scripts/predict.py --thread TARGET_THREAD_ID
```

No fallback prompt is used if the internal config/protocol/schema is unavailable. Checkpoint mode includes current work but is a simulated end-of-turn context: its wording and the desktop's runtime/display filters can affect the suggestion. Keep checkpoints factual. Prediction calls consume account usage.

Snapshots contain only the selected rollout, a temporary login-file copy, and an optional model cache in a private directory. Both Codex and SQLite homes point into it. Normal completion, errors, and handled interruption remove it; a forced process kill or power loss can leave temporary files. Never commit run state, snapshots, credentials, or prediction results.
