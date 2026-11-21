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

Follow actual Codex composer predictions as **real chat messages**, including meta suggestions and repetition, until the run limit.

Invoke `$martin-prince-loop` in the current chat. The default is five delivered predicted messages and a 30-minute dispatch budget. The agent samples the real prediction machinery, queues the exact suggestion using the installed `codex queue` command, and resumes the saved run when that message arrives. It answers or acts on the suggestion, then samples again. No separate chat or app relaunch is required by this workflow.

The loop does not stop because a suggestion is meta, repetitive, low-value, or the original task is finished. It stops at the budget, explicit user stop, null prediction, technical failure, a necessary human decision, or an unauthorized action. Generated text cannot enlarge the budget or permissions.

## Predictions and delivery

The reader fetches the live composer-prediction configuration and calls `thread/sample`. It copies the current saved rollout and closes the active turn only in that temporary copy using a factual checkpoint. This creates a fresh prediction, not a copy of cached composer text; runtime settings and display filters can differ.

The default `chat-messages` ledger records intent before sending, acceptance after the CLI returns its queue item ID, and delivery only after the exact message appears in the next active turn. An uncertain send must be reconciled rather than replayed. Same-chat continuation relies on the agent retaining the skill and state path across turns. The tested desktop messaging tool injects tool output, so this workflow uses `codex queue` instead. CLI queueing has been verified to persist the exact text as `UserInput`; consuming it into a new turn must be verified separately. An outstanding queued item must never be blindly resent.

## Install

Requires Python 3.10+, a recent Codex desktop/CLI exposing `thread/sample`, local saved history, ChatGPT credentials in `auth.json`, and the `codex queue` command. macOS is the live-tested platform; helpers use the Python standard library.

```sh
git clone https://github.com/turtlesoupy/martin-prince-loop.git
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
ln -s "$PWD/martin-prince-loop" "${CODEX_HOME:-$HOME/.codex}/skills/martin-prince-loop"
```

The repository is private; cloning requires access. Do not overwrite an existing installation. Installation starts nothing.

See [SKILL.md](SKILL.md) for the dispatch/resume workflow and [optional supervision](references/supervise.md) for another explicitly selected chat. Legacy `same-chat` mode remains available for internal execution, but is not the default.

## Privacy and validation

Source files are read-only. Temporary snapshots contain selected chat history, a login-file copy, and optional model cache in isolated Codex and SQLite homes. Normal cleanup removes them; forced termination can leave files. Keep checkpoints and run state outside the repository. Tokens are not printed. Predictions consume account usage. These internal APIs are unofficial and version-sensitive.

```sh
python3 -m unittest discover -s tests -v
```

Tests cover snapshot isolation, ledger progression, budget enforcement, exact-message reconciliation, and unexpected human input. Actual message delivery must also be verified in the running desktop app; ledger tests alone do not prove desktop queue consumption.

MIT licensed. The name and portrait are an homage; this project is not affiliated with The Simpsons or OpenAI.
