![ASCII Martin Prince saying "yolo"](assets/martin-prince.svg)

# Martin Prince Loop

A Codex skill that takes Codex's own composer prediction (the suggested next message) and sends it back as a real user message, then does it again. Meta suggestions, repeats, and "keep going" all count. It stops after five messages or 30 minutes by default.

## Install

Needs Python 3.10+, a recent Codex desktop app/CLI (`thread/sample` and `codex queue`), and a ChatGPT login in `auth.json`. Tested on macOS.

```sh
git clone https://github.com/turtlesoupy/martin-prince-loop.git
ln -s "$PWD/martin-prince-loop" "${CODEX_HOME:-$HOME/.codex}/skills/martin-prince-loop"
```

## Use

In any Codex chat:

```
$martin-prince-loop
```

Each round samples a fresh prediction from a temporary copy of the chat, queues it with `codex queue`, and picks the loop back up when the message arrives. It stops early on a null prediction, an error, a message you type yourself, or anything that needs your approval.

See [SKILL.md](SKILL.md) for the full workflow and [references/supervise.md](references/supervise.md) to drive a different chat.

## Caveats

- Relies on unofficial Codex internals that can change between versions.
- Every prediction uses your account's model quota.
- Temporary snapshots (chat history and a copy of `auth.json`) are deleted on exit, but a hard kill can leave them in your temp dir.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

MIT. Not affiliated with The Simpsons or OpenAI.
