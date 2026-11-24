![ASCII Martin Prince saying "yolo"](assets/martin-prince.svg)

# Martin Prince Loop

A Codex skill that takes Codex's own composer prediction (the suggested next message) and sends it back as a real user message, then does it again. Meta suggestions, repeats, and "keep going" all count. It stops after five messages or 30 minutes by default.

## Install

Needs Python 3.10+, a recent Codex desktop app/CLI (`thread/sample` and `codex queue`), and a ChatGPT login in `auth.json`. Tested on macOS.

```sh
git clone https://github.com/turtlesoupy/martin-prince-loop.git
mkdir -p ~/.codex/skills
ln -s "$PWD/martin-prince-loop" ~/.codex/skills/
```

If you set `CODEX_HOME`, use that in place of `~/.codex`.

## Use

In any Codex chat:

```
$martin-prince-loop
$martin-prince-loop turns=3 timeout=10m draw a sleepy kitten
```

After the skill is invoked, every follow-up message shown below is predicted and sent automatically.

<img src="assets/loop-example.png" alt="An ASCII dog gains a body and legs as Martin Prince Loop automatically sends follow-up requests." width="640">

A background worker waits for each reply to finish, samples a fresh prediction from a temporary copy of the chat, and queues it with `codex queue`. Replies aren't held up by the prediction. The loop stops early on a null prediction, an error, a message you type yourself, or anything that needs your approval.

The predictor sees the conversation without the loop's own bookkeeping, so it suggests next steps for your task rather than "keep looping".

See [SKILL.md](SKILL.md) for the full workflow and recovery steps.

## Caveats

- Relies on unofficial Codex internals that can change between versions.
- Every prediction uses your account's model quota.
- A queued message only runs if your Codex version picks up queued messages while idle.
- Temporary snapshots (chat history and a copy of `auth.json`) are deleted on exit, but a hard kill can leave them in your temp dir.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

MIT. Not affiliated with The Simpsons or OpenAI.
