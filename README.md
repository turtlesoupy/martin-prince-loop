![ASCII Martin Prince saying "yolo"](assets/martin-prince.svg)

# Martin Prince Loop

A Codex skill that takes Codex's own composer prediction (the suggested next message) and sends it back as a real user message, then does it again. Meta suggestions, repeats, and "keep going" all count. It stops after five messages or 30 minutes by default.

## Install

Needs Python 3.10+, a recent Codex desktop app/CLI (`thread/sample` and `codex queue`), and a ChatGPT login in `auth.json`. Tested on macOS.

```sh
git clone \
https://github.com\
/turtlesoupy/\
martin-prince-loop
cd \
martin-prince-loop

d=$CODEX_HOME
if [ -z "$d" ]
then
  d=~/.codex
fi
d="$d/skills"
mkdir -p "$d"
ln -s "$PWD" "$d/"
```

## Use

In any Codex chat:

```
$martin-prince-loop
```

A bounded background worker waits for the real final answer, then samples a fresh prediction and queues it with `codex queue`. The answering assistant makes one quick delivery check and replies normally; prediction happens afterward, not in front of the answer. There are no checkpoint files or per-turn prepare/send calls in the default workflow. It stops early on a null prediction, an error, a message you type yourself, or anything that needs your approval.

The predictor sees the complete conversation before invocation, then delivered messages and the work done to answer them—including ordinary tool calls and exact final replies, not prose summaries. Source boundaries exclude the skill invocation and its administration without filtering conversation topics. No hand-picked message manifest is required. This is a fresh sample, not the desktop composer's cached suggestion; normal runtime context limits still apply.

See [SKILL.md](SKILL.md) for the workflow, authorization boundaries, and recovery. The worker automatically queues suggestions, but generated requests never grant new permissions; the responding assistant reviews scope before acting. Legacy manual/cross-chat supervision is documented in [references/supervise.md](references/supervise.md).

## Caveats

- Relies on unofficial Codex internals that can change between versions.
- Every prediction uses your account's model quota and still takes time; answers no longer wait for that request.
- The worker is a bounded local process. It stops on completion, human steering, deadline, or failure; it is not a scheduled automation. Worker failures are recorded in private state/logs.
- Idle queue delivery depends on the desktop/CLI version. A queued message is not proof of delivery.
- Temporary snapshots (chat history and a copy of `auth.json`) are deleted on exit, but a hard kill can leave them in your temp dir.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

MIT. Not affiliated with The Simpsons or OpenAI.
