# Prediction-context experiment

A nine-sample comparison on November 21, 2026 used the same frozen artwork-task history and live prediction configuration, with three samples per condition. No experimental suggestion was dispatched.

| Context | Loop-continuation suggestions | Task-focused suggestions |
| --- | ---: | ---: |
| Full history + status checkpoint | 3/3 | 0/3 |
| Full history + task-only checkpoint | 3/3 | 0/3 |
| Selected task transcript + task-only checkpoint | 0/3 | 3/3 |

The task-focused suggestions asked to inspect the actual GitHub README and confirm the bubble fits, including at a narrow width. Changing only the checkpoint did not remove the feedback loop. This small, single-task comparison supports separating the transcript; it does not establish a general quality improvement or prove that the proposed checks succeed.

The default message mode now requires a task manifest. Selection is explicit and agent-reviewed, not an automatic keyword classifier; preserving substantive requirements while excluding orchestration remains part of the skill workflow. See [SKILL.md](../SKILL.md) for selection and continuation rules. Private transcripts and raw samples are kept outside this repository.
