```text
                    ___________________
              _..--'                   `--.
          _.-'                    __       `-.
        .'                    _.-'  `-.       \
       /       /          _.-'        `.       |
      /   /   /   /     .'               \     /
     /   /   /   /     /                 \_.-'
    |   /   /   /     /                      \_
    |  /   /   /  /\ /                         `.
    | /   /   /  /  V       _______      _______  |
    |/   /   /  /         .'       `.  .'       `.
    / .--.  /  /         /          \ /          \
   | /    \/  /         |          @ |          @|
   | |  _  \ /          |            |           |
    \ \   \ V            \          / `.__    __/
     `-.__/               `-.____.-'      \  /
         /                           _____/  |
        /                                    `-.
       |                      |                 |
       |                     / `----------------'
        \                   /                .'
        /`--..__                       _..-'
       /        `-------..__     __.--'
      /                     \   /  \
  _.-'---.                   \ /    |
 /        `.                  V     /\
/           \    ___________      _/  \
|            |--'          /\    /  |  |
|            |            /  \  /  ||  |
 \           /           /    \/  /||\  \
  `-._____.-'           /         ||||   |
    |                  /          ||||   |
    |                 /           `--'   |
    |                                    |
    |                                     \
    |                                      |
```

# Martin Prince Loop

Let Codex predict your next message, act on it here, then repeat—with a scope and a budget.

Invoke **`$martin-prince-loop` in the chat where you are working**. It infers the current task, obtains a real Codex prediction, checks its scope, executes it, and repeats. The default is up to five completed actions and 30 minutes. No separate controller chat, app relaunch, daemon, UI automation, or app modification is required.

## How it works

```text
  Current chat + factual progress checkpoint
                  |
                  v
  Copy saved history into a temporary Codex home
                  |
                  v
  Live composer-prediction config + real thread/sample
                  |
                  v
  Review suggestion against the user's task
                  |
                  v
  Execute here, verify, record result, repeat
```

The agent executes actions with its ordinary tools in the same active turn. The Python helpers generate predictions and maintain a durable ledger; they do not execute suggestion text as code or send chat messages. A bare invocation begins work without asking for a target chat. Missing task scope or a necessary human decision can still require clarification.

### What “real predictions” means

The reader fetches the account's actual composer-prediction configuration, applies the observed JSON-schema wrapper, and calls Codex's `thread/sample` method. It does not substitute a homemade next-action prompt.

To include work from the active turn, it copies saved history and appends a factual assistant checkpoint plus a completion marker **only to that temporary copy**. Unfinished tool calls are removed from the copy. The live chat stays active and its files are not modified.

This produces a **fresh prediction from a simulated end-of-turn checkpoint**, not the cached text displayed in the composer. Checkpoint wording, sampling, runtime context, and desktop display filters can affect the result. The snapshot uses a minimal read-only runtime. A prediction does not expand the user's authorization.

## Install

Requires Python 3.10+, a recent Codex desktop/CLI with `thread/sample`, local paginated conversation history, and a ChatGPT login stored in `CODEX_HOME/auth.json`. The implementation targets macOS/Linux; only macOS has been live-tested. Keychain-only credentials, API-key-only logins, cloud-only threads, and Windows file locking are not supported.

```sh
git clone https://github.com/turtlesoupy/martin-prince-loop.git
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
ln -s "$PWD/martin-prince-loop" \
  "${CODEX_HOME:-$HOME/.codex}/skills/martin-prince-loop"
```

Do not replace an existing skill at that destination. This repository is initially private, so cloning requires access. Installation starts nothing. Python helpers use only the standard library.

## Run the skill

In your current work chat:

> $martin-prince-loop

Or give explicit scope and limits:

> Use $martin-prince-loop to finish and verify this settings panel, up to three actions. Do not publish or deploy anything.

The agent quotes each accepted prediction and executes it. It stops on a null or repeated suggestion, exhausted budget, new human input, an unrelated action, or a necessary human decision. The time limit prevents starting new actions; work already executing can finish after it. Runs are bounded and do not install a background service or scheduler.

Only if you explicitly want to supervise another existing chat, use the optional [supervision mode](references/supervise.md). That mode uses desktop chat messaging tools and is not required for ordinary use.

### Helper commands

The skill manages these commands. Same-chat mode detects the current chat from `CODEX_THREAD_ID` or `CODEX_SESSION_ID`:

```sh
python3 scripts/loop.py init --state /tmp/martin-run.json \
  --scope 'Finish and verify the settings panel' \
  --max-iterations 5 --max-minutes 30
# Write a factual progress summary to /tmp/martin-checkpoint.txt first.
python3 scripts/loop.py next --state /tmp/martin-run.json \
  --checkpoint-file /tmp/martin-checkpoint.txt
python3 scripts/loop.py begin-step --state /tmp/martin-run.json \
  --response-id RESPONSE_ID --review 'Why this action is in scope'
# Execute and verify the action; write evidence to /tmp/martin-result.txt.
python3 scripts/loop.py finish-step --state /tmp/martin-run.json \
  --result-file /tmp/martin-result.txt
python3 scripts/loop.py status --state /tmp/martin-run.json
```

Each subsequent prediction needs a new factual checkpoint. The ledger enforces sequencing, unchanged human input, repetition checks, and budgets. It records the agent's verification evidence; it cannot independently prove that the action succeeded. An interrupted executing step must be reconciled, not blindly replayed. Use `loop.py stop --state STATE --reason REASON` to stop a run.

### Preview one prediction

```sh
# Current active turn:
python3 scripts/predict.py --checkpoint-file /tmp/martin-checkpoint.txt
# Last completed saved turn of a selected chat:
python3 scripts/predict.py --thread YOUR_LOCAL_THREAD_ID
```

Without a checkpoint, the reader excludes any in-progress turn. Add `--require-idle` to reject busy or failed targets. Use `--codex-home`, `--sqlite-home`, or `--cli` for nonstandard installations. Unsupported database/protocol/config fails explicitly; there is no substitute-prompt fallback.

## Privacy and operational limits

- Only the selected chat's saved rollout is copied. Source databases are opened read-only and the app's writer lock is left alone.
- An isolated private directory contains the snapshot, a temporary login-file copy, and an optional model cache. Both Codex home and SQLite home point into it.
- Normal completion, errors, and handled interruption clean up that directory. Force-killing the process or losing power can leave it behind. Use `--scratch` to choose its parent directory.
- Login tokens are not printed or included in result files. The live prediction prompt is fetched at runtime, not bundled in the repository.
- Predictions consume account usage. Run state and checkpoints contain conversation-derived text; keep them outside this repository and treat them as private.
- This relies on internal, version-sensitive interfaces and SQLite schemas. It is an unofficial experiment, not an OpenAI-supported integration.

## Validation

```sh
python3 -m unittest discover -s tests -v
```

Synthetic tests cover snapshot isolation, active-turn checkpoints, cleanup, same-chat progression, human steering, budget enforcement, replay prevention, and optional supervision bookkeeping. Live sampling has returned real suggestions from active-turn checkpoints while the desktop app remained open. Installation does not run a live test or begin a loop.

MIT licensed. The name and ASCII portrait are an homage; this project is not affiliated with The Simpsons or OpenAI.
