# Architecture decision records

Decisions that shaped the app and would be expensive to make again: what was decided, what else was considered, and
the evidence. One file per decision, numbered in the order they were made. A decision that is replaced keeps its file;
its status says by which record.

| # | Decision | Status |
|---|---|---|
| [1](0001-own-beat-tracker.md) | Our own causal beat tracker instead of a beat-tracking library | accepted |
| [2](0002-line-in-is-the-precision-target.md) | Line-level audio is the precision target; a microphone is best effort | accepted |
| [3](0003-capture-with-gstreamer-pipewire.md) | Audio capture through GStreamer's PipeWire source | accepted |
| [4](0004-timestamps-from-arrival-fit.md) | Capture times from a fit of arrival times, not from PipeWire's timestamps | accepted |
| [5](0005-pause-previews-on-analysis-age.md) | "Can the beat be had in time?" is judged by the age of the analysed audio, not by CPU load | accepted |
| [6](0006-clock-keeps-its-bar-downbeat-is-manual.md) | The audio clock takes the tracker's phase and tempo but keeps its own bar; the downbeat stays manual | accepted |
| [7](0007-benchmark-scores-what-the-projector-shows.md) | Beat trackers are benchmarked on what the projector would show, with Beat This! as the reference | accepted |

## Template

```markdown
# N. Title: the decision, in a sentence

- Status: proposed | accepted | replaced by ADR M
- Date: YYYY-MM-DD

## Context
What forced a decision, and what the solution has to do.

## Options considered
## How it was tested   (when there was evidence)
## Decision
## Consequences
What is easier now, what is harder, what to watch, and when to revisit it.
```
