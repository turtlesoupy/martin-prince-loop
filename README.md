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
         /                                 | |
        /                            _____/  |
       /                                     `-.
      |                                         \
      |                       |                  |
      |                      / `----------------'
       \                    /                 |
        \                                    /
         `-.                               .'
        /   `--..__                    _.-'
       /           `----..__     __.--'
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

Let Codex predict your next message, act on it, then repeat—with a scope and a budget.

This is a Codex skill plus a snapshot-based prediction reader and a durable controller ledger. It uses the **live composer-prediction prompt** and Codex's **`thread/sample`** method. It does not replace suggestions with a homemade “what should I do next?” prompt.

**No app relaunch, daemon, UI automation, or app modification required.**

## How it works

```text
  Existing work chat finishes a turn
                  |
                  v
  Copy that chat's completed history into a temporary Codex home
                  |
                  v
  Live prediction config + real thread/sample
                  |
                  v
  Controller checks the suggestion against your scope
                  |
                  v
  Send the exact suggestion to the work chat
                  |
                  v
  Wait, verify completion, record it, repeat
```

The controller runs in a separate Codex chat and uses the desktop's existing chat messaging and waiting tools. The Python scripts handle prediction and run-state bookkeeping; they **never send a message themselves**. A scope check happens before each send. The controller stops on its budget, a null or repeated suggestion, a failed/interrupted turn, unexpected human input, or work outside your authorization.

### What “real predictions” means

The reader fetches the account's actual prediction configuration from the endpoint used by the app, applies the observed JSON-schema wrapper, and calls the same `thread/sample` method on a completed-turn snapshot.

This produces a **fresh sample**, not a copy of the text already displayed in the composer. Sampling, runtime context, and desktop display filters can make the results differ. The snapshot uses a minimal read-only runtime rather than copying every app setting. Nothing here claims that a generated suggestion is automatically useful or authorized.

## Install

Requires Python 3.10+, a recent Codex desktop/CLI with `thread/sample`, local paginated conversation history, and a ChatGPT login stored in `CODEX_HOME/auth.json`. The implementation currently targets macOS/Linux; only macOS has been live-tested. Keychain-only credentials, API-key-only logins, cloud-only threads, and Windows controller locking are not supported.

```sh
git clone https://github.com/turtlesoupy/martin-prince-loop.git
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
ln -s "$PWD/martin-prince-loop" \
  "${CODEX_HOME:-$HOME/.codex}/skills/martin-prince-loop"
```

Do not replace an existing skill at that destination. This repository is initially private, so cloning requires access. Installation starts nothing. Python helpers use only the standard library.

## Run the skill

In a **separate controller chat**, identify an existing work chat and give it a scope:

> Use $martin-prince-loop on my existing “Fix the settings panel” chat. You may send it up to five predicted follow-up messages over the next 30 minutes, limited to fixing and verifying that panel. Do not publish or deploy anything.

The controller resolves the target, records scope and limits, then uses the real predictions verbatim. It keeps working through the finite run; it does not install a background service or scheduler. Do not send other messages in the target while a step is being dispatched. You can steer or stop the loop from the controller chat.

The default budget is five completed follow-ups and 30 minutes for new dispatches. A target turn already in progress may finish after that deadline. A “forever” request needs a finite budget before starting. The skill will not silently create another chat if invoked inside its own target.

### Preview one prediction

```sh
python3 scripts/predict.py \
  --thread YOUR_LOCAL_THREAD_ID \
  --output /tmp/martin-prediction.json
```

This works while the desktop app is open. It excludes any in-progress turn and samples the last completed saved turn. Add `--require-idle` to reject a busy or failed target instead. Use `--codex-home`, `--sqlite-home`, or `--cli` for nonstandard installations. An unsupported database/protocol/config fails explicitly; there is no substitute-prompt fallback.

### Controller bookkeeping

The skill manages these commands; they are also useful for inspection:

```sh
python3 scripts/loop.py init --state /tmp/martin-run.json \
  --thread YOUR_LOCAL_THREAD_ID --scope 'Fix and verify the settings panel' \
  --max-iterations 5 --max-minutes 30
python3 scripts/loop.py next --state /tmp/martin-run.json
python3 scripts/loop.py status --state /tmp/martin-run.json
python3 scripts/loop.py stop --state /tmp/martin-run.json --reason 'User stopped the loop'
```

`prepare-send` records reviewed dispatch intent **before** the skill calls the messaging tool. `sent` records tool acceptance. `complete-step` requires the exact expected user message and a completed target turn before advancing. If delivery is uncertain, the controller reconciles the target instead of resending. The connector has no atomic expected-turn guard, so there remains a small check/send race; this is not an exactly-once delivery guarantee. The scripts enforce sequencing, freshness, repetition, and budgets. The skill agent—not a keyword filter—judges scope and usefulness.

## Privacy and operational limits

- Only the selected chat's completed rollout prefix is copied. The source databases are opened read-only and the running app's writer lock is left alone.
- An isolated temporary directory contains the snapshot, a temporary login-file copy, and an optional model cache. Both Codex home and SQLite home point into it.
- Normal completion, errors, and handled interruption clean up that directory. Force-killing the process or losing power can leave it behind. Use `--scratch` if you want a known cleanup location.
- The login is used only for the existing OpenAI account flow. Tokens are not printed or included in result files. The live prediction prompt is fetched at runtime, not bundled in the repository.
- Predictions consume account usage. Run state and prediction files contain conversation-derived text; keep them outside this repository and treat them as private.
- Stopping the controller prevents new sends. It does not automatically interrupt work already dispatched.
- This relies on internal, version-sensitive interfaces and SQLite schemas. It is an unofficial experiment, not an OpenAI-supported integration.

## Validation

```sh
python3 -m unittest discover -s tests -v
```

Synthetic tests exercise snapshot isolation and cleanup, stale-source detection, bounded progression, exact-message completion, and ambiguous-send recovery. The prediction reader has also returned real suggestions while the desktop app remained open. The full desktop connector loop has **not** been end-to-end tested against a real work chat; no such test is run automatically during installation.

MIT licensed. The name and ASCII portrait are an homage; this project is not affiliated with The Simpsons or OpenAI.
