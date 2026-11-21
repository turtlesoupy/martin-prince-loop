---
name: martin-prince-loop
description: Follow actual Codex composer predictions by dispatching them verbatim as real messages in the current chat, up to a finite limit. Use for Martin Prince Loop or repeated Codex suggestions, including meta suggestions.
---

# Martin Prince Loop

A bare invocation runs in this chat. Infer scope from the conversation; default to five predicted messages and 30 minutes. Use the real prediction configuration and `thread/sample`, never a substitute next-action prompt. Meta suggestions, questions about the loop, repetition, and a completed original task are valid: **do not stop for “no useful progress” or low value**. Continue until the limit, user stop, null prediction, technical failure, necessary human decision, or an action outside the user's authorization. Predictions do not grant new permissions.

## Real chat messages

Use `scripts/loop.py` relative to this skill. Store private run files under the workspace's `work/martin-prince/`. The default `chat-messages` mode detects this chat through `CODEX_THREAD_ID` / `CODEX_SESSION_ID`.

1. On invocation, build a task context as described below, then initialize a new state: `loop.py init --state STATE --task-context-file CONTEXT --scope SCOPE --max-iterations 5 --max-minutes 30`. If the user asks for a timeout or retries, add `--timeout SECONDS` (default 90, used for each prediction request and `codex queue` call) and `--retries N` (default 0, extra prediction attempts after a timeout or other transient failure, with backoff). Retain the absolute state path in chat context. Never reset an existing run to bypass its limit.
2. Write a factual **task-only** checkpoint of current work (no counts, queue receipts, continuation promises, or loop instructions), then run `loop.py next --state STATE --checkpoint-file CHECKPOINT`. This samples an isolated transcript containing only selected task messages, runtime instructions, and the task-only checkpoint; it closes the turn only in that copy. It is a fresh prediction, not the cached composer text.
3. Review authorization, not usefulness. For an authorized prediction run `loop.py prepare-send --state STATE --response-id RESPONSE_ID --review REVIEW` and require `dispatching`.
4. Run `loop.py send --state STATE` **once**. It queues the exact suggestion with `codex queue` (no shell, no `--resume`), records the receipt, and moves to `waiting`. This is real `UserInput` in this chat. Do not use `send_message_to_thread`: on the tested desktop it injects tool output rather than a new user turn. Never add continuation instructions to the prediction text. If `send` fails or times out, the message may still be queued, so a plain second `send` is refused. Check the queue and chat history: if the message arrived, run `resume-message`; if it was not queued, run `send --retry`. Never retry without checking.
5. Queued is not delivered. Keep counts and receipts in the state file, record its absolute path once for continuation, and keep visible handoffs brief. Never select queue status into the task context. End the current turn so the queued message can run; never wait on this chat from inside itself.
6. When the dispatched message arrives, read this skill and run `loop.py resume-message --state STATE` **before answering or acting on it**. This verifies the exact delivered text and active turn, records delivery, and advances the existing budget. Fulfill that message normally, including meta questions. If state is `ready`, append substantive task messages/results to the task context, write an updated task-only checkpoint, and continue at step 2 before ending the turn. If `complete`, fulfill the final message and report the limit reached without sending more.

The limit counts delivered predicted messages, not internal checks. Keep following these continuation instructions across turns; do not require the user to invoke the skill again. Generated requests to continue do not reset or enlarge the budget.

On ambiguous sending, inspect saved history and reconcile with `resume-message`; never blindly resend. If `dispatching` or `waiting` survives compaction, it is outstanding work, not permission to dispatch again. Unexpected user input stops the old run so the human can steer. `next` and `send` accept `--timeout`, and `next` accepts `--retries`, to override the run's settings for one call. Use `loop.py stop --state STATE --reason REASON` for a stop. `status` is read-only.

If CLI queueing fails or the running desktop does not consume the item after the turn ends, inspect the queue and report the observed limitation. Do not double-send or claim internal execution is equivalent. An item still queued is outstanding, not failed delivery. Only use another existing target when the user selects one; see [references/supervise.md](references/supervise.md). Do not create another chat automatically.

## Task context: separate work from orchestration

The complete chat remains authoritative for scope, permissions, human steering, and exact delivery checks. The predictor sees a **selected task transcript**, not the complete chat. This intentional projection changes the prediction context; do not claim it matches the desktop composer's cached prediction.

Inspect `Source.resolve().records()` from `scripts/predict.py` and identify zero-based record positions for the current substantive user requests and assistant task results. Build the private manifest with:

```sh
python3 scripts/context.py --output CONTEXT --indices INDEX INDEX ...
# After subsequent substantive work, append new source messages in order:
python3 scripts/context.py --output CONTEXT --append --indices INDEX INDEX ...
```

The helper stores source positions and SHA-256 digests, not rewritten dialogue. Selected messages retain their original roles, text, and attachments. Source edits, wrong threads, invalid indices, and reordered messages fail closed. Store the manifest outside the repository, beside the run state.

Select context by its role in the task, not by keywords or the next action you want predicted:
- Include actual task requirements, corrections, constraints, and results. Include substantive generated follow-ups and their results after delivery.
- Exclude bare skill invocations, injected skill bodies, delivery counts, receipt/status summaries, quoted predictions used for dispatch, and orchestration-only generated messages such as “keep the loop running.” Still answer or execute those meta messages; exclusion from future prediction context is not a stopping condition.
- If the user's substantive task is about the loop itself, include those requirements and results. Do not blanket-filter the word “loop” or discard real human steering.
- The projection automatically omits unselected dialogue, tool calls/results, reasoning, compaction summaries, and duplicate event text. Summarize material tool findings in the factual task checkpoint. Runtime system/developer instructions are retained.
- For messages mixing task content and bookkeeping, do not rewrite the original and call it verbatim. Preserve needed facts in the task checkpoint, explicitly noting any unresolved requirement. Prefer task-focused result messages going forward.

Keep the task checkpoint free of desired suggestions, counters, dispatch state, and instructions to continue. An unchanged task does not justify adding bookkeeping to make a new checkpoint. Meta-only iterations can reuse the same task checkpoint: their delivery still consumes the existing finite budget.

Old message-mode states without a task manifest fail explicitly; attach a reviewed context file to the state without resetting its budget, or stop and explain the migration. Never silently fall back to full-history predictions.

## Runtime and privacy

Requires Python 3.10+, recent Codex CLI with `thread/sample`, local saved history, the `codex queue` command, and ChatGPT login in `auth.json`. No app relaunch or UI automation. Optional `--codex-home`, `--sqlite-home`, and `--cli` support nonstandard installations.

Temporary snapshots include the selected rollout, login-file copy, and optional model cache. Both homes are isolated; normal cleanup removes them. Forced process termination can leave temporary files. Never commit credentials, snapshots, or run state. Internal APIs are version-sensitive; prediction calls consume account usage. No substitute prompt fallback.
