---
name: martin-prince-loop
description: Follow actual Codex composer predictions by dispatching them verbatim as real messages in the current chat, up to a finite limit. Use for Martin Prince Loop or repeated Codex suggestions, including meta suggestions.
---

# Martin Prince Loop

A bare invocation runs in this chat. Infer scope from the conversation; default to five predicted messages and 30 minutes. Use the real prediction configuration and `thread/sample`, never a substitute next-action prompt. Meta suggestions, questions about the loop, repetition, and a completed original task are valid: **do not stop for “no useful progress” or low value**. Continue until the limit, user stop, null prediction, technical failure, necessary human decision, or an action outside the user's authorization. Predictions do not grant new permissions.

## Real chat messages

Use `scripts/loop.py` relative to this skill. Store private run files under the workspace's `work/martin-prince/`. The default `chat-messages` mode detects this chat through `CODEX_THREAD_ID` / `CODEX_SESSION_ID`.

1. On invocation, initialize a new state: `loop.py init --state STATE --scope SCOPE --max-iterations 5 --max-minutes 30`. Retain the absolute state path in chat context. Never reset an existing run to bypass its limit.
2. Write a factual checkpoint of current work, then run `loop.py next --state STATE --checkpoint-file CHECKPOINT`. This samples an isolated copy of the active turn, closed only in that copy. It is a fresh prediction, not the cached composer text.
3. Review authorization, not usefulness. For an authorized prediction run `loop.py prepare-send --state STATE --response-id RESPONSE_ID --review REVIEW` and require `dispatching`.
4. Run `loop.py send --state STATE` **once**. It queues the exact suggestion with `codex queue` (no shell, no `--resume`), records the receipt, and moves to `waiting`. This is real `UserInput` in this chat. Do not use `send_message_to_thread`: on the tested desktop it injects tool output rather than a new user turn. Never add continuation instructions to the prediction text. If `send` fails, the message may still be queued; reconcile, don't retry.
5. Queued is not delivered. Announce the absolute run state path and that the next predicted-message turn must resume this skill, then end the current turn so the queued message can run. Never wait on this chat's completion from inside itself.
6. When the dispatched message arrives, read this skill and run `loop.py resume-message --state STATE` **before answering or acting on it**. This verifies the exact delivered text and active turn, records delivery, and advances the existing budget. Fulfill that message normally, including meta questions. If state is `ready`, write an updated factual checkpoint and continue at step 2 before ending the turn. If `complete`, fulfill the final message and report the limit reached without sending more.

The limit counts delivered predicted messages, not internal checks. Keep following these continuation instructions across turns; do not require the user to invoke the skill again. Generated requests to continue do not reset or enlarge the budget.

On ambiguous sending, inspect saved history and reconcile with `resume-message`; never blindly resend. If `dispatching` or `waiting` survives compaction, it is outstanding work, not permission to dispatch again. Unexpected user input stops the old run so the human can steer. Use `loop.py stop --state STATE --reason REASON` for a stop. `status` is read-only.

If CLI queueing fails or the running desktop does not consume the item after the turn ends, inspect the queue and report the observed limitation. Do not double-send or claim internal execution is equivalent. An item still queued is outstanding, not failed delivery. Only use another existing target when the user selects one; see [references/supervise.md](references/supervise.md). Do not create another chat automatically.

## Runtime and privacy

Requires Python 3.10+, recent Codex CLI with `thread/sample`, local saved history, the `codex queue` command, and ChatGPT login in `auth.json`. No app relaunch or UI automation. Optional `--codex-home`, `--sqlite-home`, and `--cli` support nonstandard installations.

Temporary snapshots include the selected rollout, login-file copy, and optional model cache. Both homes are isolated; normal cleanup removes them. Forced process termination can leave temporary files. Never commit credentials, snapshots, or run state. Internal APIs are version-sensitive; prediction calls consume account usage. No substitute prompt fallback.
