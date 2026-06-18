# Stream Hygiene v12

v12 fixes a generic controller problem discovered by Biopsy: the agent may be working correctly, but the browser stream can still fail if the server sends raw model responses, full prompts, whole rewritten source files, or full run transcripts as NDJSON events.

## Failure mode

A model can emit a long response with several `<function_calls>` blocks, a large `file_write` payload, stdout/stderr, and a final answer. Even when the atomic controller executes only one action, earlier versions still streamed large raw events to the browser. The browser trace and Biopsy could balloon into multi-megabyte objects and eventually show a vague `network error`.

## Generic fix

The agent now separates three layers:

1. **Execution truth** — tools still receive the real arguments they need.
2. **Lab notebook** — the model sees compact observations: summaries, stdout/stderr snippets, and hashes.
3. **Browser stream / Biopsy** — large fields are represented as preview + character count + hash.

This avoids symptom-specific patches. The rule is universal: no browser event should be treated as a raw archive.

## What is compacted

- raw model responses
- prompts/messages
- tool args containing source code
- transcript fields
- run rows
- stdout/stderr/trace fields
- final events containing full transcript payloads

Large values keep:

- preview
- original character count
- truncated character count
- short SHA-256 hash

## Why this matters

Biopsy remains useful because it shows what happened, but it no longer tries to carry the whole universe through one browser event. This supports the atomic loop:

```text
small action → compact observation → next action → compact observation
```

## Design rule going forward

Do not fix one payload at a time. Any new tool or feature should pass through the same stream compaction layer before reaching the browser.
