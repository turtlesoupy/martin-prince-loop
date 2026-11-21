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

The default `chat-messages` ledger records intent before sending, acceptance after the CLI returns its queue item ID, and delivery only after the exact message appears in the next active turn. An uncertain send must be reconciled rather than replayed. Same-chat continuation relies on the agent retaining the skill and state path across turns. The tested desktop messaging tool injects tool output, so this workflow uses `codex queue` instead. CLI queueing persists the exact text as `UserInput`. The desktop consumes the queued message after the current turn ends, and the next turn resumes the same run. These turn boundaries are handoffs, not loop stops. An outstanding queued item must never be blindly resent.

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

The 31 tests cover snapshot isolation, ledger progression, budget enforcement, exact-message reconciliation, and unexpected human input.

A live macOS run on November 21, 2026 verified **five real predicted messages delivered into five distinct turns** through `codex queue`, without restarting the app. Each delivery matched its saved prediction, including meta requests to continue. The ledger reached `complete` at exactly five deliveries, and no sixth message was sent. The first queued item was reconciled into the existing run after the earlier messaging-tool experiment failed; subsequent predictions proceeded through the normal queue/resume workflow. Private run state and conversation history are not included in this repository.

This verifies the tested desktop/CLI version, not compatibility with every version. Each run still verifies delivery rather than treating queue acceptance as completion.

MIT licensed. The name and portrait are an homage; this project is not affiliated with The Simpsons or OpenAI.
