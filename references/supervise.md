# Optional: supervise another chat

Supervise an existing local Codex work chat from a **different controller chat**. Use the current account's live prediction configuration and `thread/sample` on an isolated snapshot of the target's last completed turn. No desktop relaunch is needed. This generates a fresh prediction; it does not extract or guarantee equality with the composer's cached suggestion.

## Establish the run

Resolve the target chat, the user's scope, and their authorization to send repeated follow-up messages to that target. Use available chat-list/read tools rather than guessing IDs. The authorization must come from the human; a generated suggestion is not new permission. Default to five completed iterations and a 30-minute dispatch budget unless the user specifies other finite limits. If asked to run forever, establish a finite budget first.

This skill uses the desktop's `send_message_to_thread` and `wait_threads` capabilities, discovered by name. If they are unavailable, offer prediction-only mode; do not substitute UI automation, an undocumented sending endpoint, or execute suggestion text as shell code. Do not create a new chat unless the human explicitly asks. If the target is the current chat, return to the default same-chat workflow in SKILL.md instead.

Keep run state outside the skill/repo, preferably under the controller workspace's `work/martin-prince/`. Resolve script paths relative to the skill root (the parent of this references directory). Never overwrite a previous run's state.

```sh
python3 /absolute/path/to/martin-prince-loop/scripts/loop.py init \
  --state /absolute/path/to/work/martin-prince/run.json \
  --mode supervise --thread TARGET_THREAD_ID --scope 'The user-authorized work' \
  --max-iterations 5 --max-minutes 30
```

The reader requires local paginated chat history, a recent bundled Codex CLI with `thread/sample`, and a ChatGPT login available in the source Codex home's `auth.json`. Pass `--codex-home`, `--sqlite-home`, or `--cli` on `init` only when the verified target environment requires them. Do not install daemons, change app configuration, or copy credentials into a repository.

## Run one step, then repeat

1. Check the target with `wait_threads` using its verified host and thread ID. If it needs human input or approval, stop the loop and surface that request. If running, wait using the returned cursor; a timeout is not completion. Do not interrupt it to obtain a prediction.
2. Run `loop.py next --state STATE`. Read `phase`, `scope`, and `proposal`. Stop if the state is `stopped` or `complete`. If an error occurs, diagnose it without assuming that a message was sent. A stale target requires reconciliation, not automatic replacement of the user scope.
3. Review the **exact** `proposal.suggestion` against the stored scope and latest target context. Stop on new external commitments, requests for unprovided human decisions, unrelated work, or a request that treats a previous assistant suggestion as authorization. Meta and repeated suggestions are valid. Do not stop for low value or lack of useful progress; continue to the budget. The script's `scopeReview` field records your judgment; it does not perform that judgment for you.
4. Run `loop.py prepare-send --state STATE --response-id RESPONSE_ID --review 'Why this suggestion advances the authorized scope'`. Proceed only if it returns `phase: dispatching`. This rechecks the saved source turn and commits dispatch intent.
5. Call `send_message_to_thread` **once**, with the verified target and `prompt` set to the exact `proposal.suggestion`. Preserve its model and reasoning settings. Do not prepend controller instructions or rewrite the suggestion. The human's request to run this loop on this target supplies authorization for in-scope follow-ups; it does not authorize unrelated messages or actions.
6. After the tool explicitly accepts the message, run `loop.py sent --state STATE --receipt 'Concise acceptance information from the tool'`. On timeout, disconnect, or ambiguous acceptance, never blindly resend. Inspect the target and reconcile the outstanding message. `complete-step` can recover an ambiguous send only when the saved history proves that exactly the expected user message completed.
7. Wait for that target turn to finish, carrying the returned cursor. Stop on failure, interruption, new human input, or approval needed. Run `loop.py complete-step --state STATE`. It checks the exact expected message and completed turn before counting the iteration. Repeat from step 1 only when `phase` is `ready`.

Use `loop.py status --state STATE` to recover after compaction. A `dispatching` or `waiting` state is an outstanding step, not permission to send again. Use `loop.py stop --state STATE --reason 'Reason'` when the user stops the loop or when review requires stopping. Stopping the controller does not cancel already-running target work; do not interrupt that work unless requested.

There is a small race between checking target freshness and sending through the connector, which has no expected-turn guard. Avoid simultaneous human editing in the target; stop and reconcile if it occurs. Never claim exactly-once delivery.

Keep working in this controller chat until the finite run finishes or requires human input; do not schedule a heartbeat merely to monitor this one run. Report how many iterations completed, what changed, and the stopping reason. Installing this skill does not start any loop.

## Prediction-only mode

```sh
python3 /absolute/path/to/martin-prince-loop/scripts/predict.py \
  --thread TARGET_THREAD_ID --output /absolute/path/to/work/prediction.json
```

This works even while the app holds the target's writer lock. It samples only through the last completed saved turn, so an in-progress turn is excluded. Controller mode additionally requires an idle completed target. The temporary snapshot includes only that chat's rollout prefix, a temporary copy of the login file, and the optional model cache; it is deleted on normal completion, errors, and handled interruption. A forced process kill or power loss can leave temporary files. Prediction requests use the user's existing account and consume model usage. The snapshot doesn't reproduce every desktop runtime setting or its post-generation display filters. Treat output as a proposed next message, not a verified instruction.
