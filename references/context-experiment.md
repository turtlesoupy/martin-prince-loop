# Prediction-context experiment

A nine-sample comparison on November 21, 2026 used the same frozen artwork-task history and live prediction configuration, with three samples per condition. No experimental suggestion was dispatched.

| Context | Loop-continuation suggestions | Task-focused suggestions |
| --- | ---: | ---: |
| Full history + status checkpoint | 3/3 | 0/3 |
| Full history + task-only checkpoint | 3/3 | 0/3 |
| Selected task transcript + task-only checkpoint | 0/3 | 3/3 |

The task-focused suggestions asked to inspect the actual GitHub README and confirm the bubble fits, including at a narrow width. Changing only the checkpoint did not remove the feedback loop. This small, single-task comparison supports separating the transcript; it does not establish a general quality improvement or prove that the proposed checks succeed.

The skill now gets the same separation without a hand-picked transcript: it records where its own turns start and leaves those ranges out of the copy it samples from. See [SKILL.md](../SKILL.md). Private transcripts and raw samples are kept outside this repository.
