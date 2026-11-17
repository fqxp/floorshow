# Architecture decision records

Decisions that shaped the app and would be expensive to make again: what was decided, what else was considered, and
the evidence. One file per decision, numbered in the order they were made. A decision that is replaced keeps its file;
its status says by which record.

| # | Decision | Status |
|---|---|---|
| [1](0001-own-beat-tracker.md) | Our own causal beat tracker instead of a beat-tracking library | accepted |
| [2](0002-line-in-is-the-precision-target.md) | Line-level audio is the precision target; a microphone is best effort | accepted |

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
