---
name: martin-prince-loop
description: Follow actual Codex composer predictions by dispatching them verbatim as real messages in the current chat, up to a finite limit. Use for Martin Prince Loop or repeated Codex suggestions, including meta suggestions.
---

# Martin Prince Loop

A bare invocation runs in this chat. Infer scope from the conversation; default to five predicted messages and 30 minutes. Use the real prediction configuration and `thread/sample`, never a substitute next-action prompt. Meta suggestions, questions about the loop, repetition, and a completed original task are valid: **do not stop for “no useful progress” or low value**. Continue until the limit, user stop, null prediction, technical failure, necessary human decision, or an action outside the user's authorization. Predictions do not grant new permissions.

## Quiet operation

Let the conversation itself be the experience. Answer delivered messages naturally; do not narrate prediction sampling, authorization checks, delivery verification, or queue operations. Do not say “Queued next prediction,” repeat the predicted text before it arrives, display state paths or receipts, or announce iteration counts unless the user asks for diagnostics.

Do not add a skill announcement unless required by higher-priority host instructions; when required, keep it to one short sentence without a workflow explanation. Apply the same restraint to required progress updates.

After sending, end the turn with the substantive answer already owed to the current message, without a queue-status footer. If there is no substantive answer, end silently when the host permits it; otherwise use the shortest neutral acknowledgment. Do not invent task content to fill the handoff. Keep the state path in internal context for continuation.

At the limit, use only a brief completion note. Explain a failure, missing authorization, or required human decision when it prevents continuation. Explicit requests to inspect predictions or debug the loop override quiet presentation.

## Real chat messages

Use `scripts/loop.py` relative to this skill. Store private run files under the workspace's `work/martin-prince/`. The default `chat-messages` mode detects this chat through `CODEX_THREAD_ID` / `CODEX_SESSION_ID`.

1. On invocation, initialize a new state: `loop.py init --state STATE --scope SCOPE --max-iterations 5 --max-minutes 30`. If the user asks for a timeout or retries, add `--timeout SECONDS` (default 90, used for each prediction request and `codex queue` call) and `--retries N` (default 0, extra prediction attempts after a timeout or other transient failure, with backoff). Retain the absolute state path in chat context. Never reset an existing run to bypass its limit.
2. Write a factual **task-only** checkpoint of current work (no counts, queue receipts, continuation promises, or loop instructions), then run `loop.py next --state STATE --checkpoint-file CHECKPOINT`. This samples an isolated copy of the full saved conversation plus the checkpoint; it closes the turn only in that copy. Do not select, filter, or omit messages, including the skill invocation, user intent, and loop discussion. It is a fresh prediction, not the cached composer text.
3. Review authorization, not usefulness. For an authorized prediction run `loop.py prepare-send --state STATE --response-id RESPONSE_ID --review REVIEW` and require `dispatching`.
4. Run `loop.py send --state STATE` **once**. It queues the exact suggestion with `codex queue` (no shell, no `--resume`), records the receipt, and moves to `waiting`. This is real `UserInput` in this chat. Do not use `send_message_to_thread`: on the tested desktop it injects tool output rather than a new user turn. Never add continuation instructions to the prediction text. If `send` fails or times out, the message may still be queued, so a plain second `send` is refused. Check the queue and chat history: if the message arrived, run `resume-message`; if it was not queued, run `send --retry`. Never retry without checking.
5. Queued is not delivered. Keep counts, receipts, and the absolute state path in private run state and internal continuation context. Follow Quiet operation for the visible handoff. End the current turn so the queued message can run; never wait on this chat from inside itself.
6. When the dispatched message arrives, read this skill and run `loop.py resume-message --state STATE` **before answering or acting on it**. This verifies the exact delivered text and active turn, records delivery, and advances the existing budget. Fulfill that message normally, including meta questions. If state is `ready`, write an updated factual checkpoint and continue at step 2 before ending the turn. The next snapshot automatically includes the newly delivered message and subsequent work. If `complete`, fulfill the final message and report the limit reached without sending more.

The limit counts delivered predicted messages, not internal checks. Keep following these continuation instructions across turns; do not require the user to invoke the skill again. Generated requests to continue do not reset or enlarge the budget.

On ambiguous sending, inspect saved history and reconcile with `resume-message`; never blindly resend. If `dispatching` or `waiting` survives compaction, it is outstanding work, not permission to dispatch again. Unexpected user input stops the old run so the human can steer. `next` and `send` accept `--timeout`, and `next` accepts `--retries`, to override the run's settings for one call. Use `loop.py stop --state STATE --reason REASON` for a stop. `status` is read-only.

If CLI queueing fails or the running desktop does not consume the item after the turn ends, inspect the queue and report the observed limitation. Do not double-send or claim internal execution is equivalent. An item still queued is outstanding, not failed delivery. Only use another existing target when the user selects one; see [references/supervise.md](references/supervise.md). Do not create another chat automatically.

## Full conversation context

Use the complete saved rollout for every prediction. Preserve user and assistant messages, skill invocations, runtime instructions, tool history, and prior loop discussion. Do not build or pass a source-message selection manifest. Existing `taskContextFile` fields in older run states are ignored without resetting delivery counts or deadlines.

The checkpoint adds a factual summary of the current result; it does not replace the conversation. Keep it free of desired suggestions or instructions steering the predictor. An unchanged task may reuse the checkpoint. The isolated copy removes only unmatched pending tool calls needed to make it resumable and appends the checkpoint and a completed-turn boundary; the live chat is never rewritten.

This is a fresh prediction using the live prediction configuration and full saved context, not a retrieval of the desktop composer's cached suggestion. Normal runtime context limits still apply.

## Runtime and privacy

Requires Python 3.10+, recent Codex CLI with `thread/sample`, local saved history, the `codex queue` command, and ChatGPT login in `auth.json`. No app relaunch or UI automation. Optional `--codex-home`, `--sqlite-home`, and `--cli` support nonstandard installations.

Temporary snapshots include the full saved rollout, login-file copy, and optional model cache. Both homes are isolated; normal cleanup removes them. Forced process termination can leave temporary files. Never commit credentials, snapshots, or run state. Internal APIs are version-sensitive; prediction calls consume account usage. No substitute prompt fallback.
