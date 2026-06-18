# SoulAgentOS v13 — Skill Finder / Pattern Fovea

This release adds a generic skill-discovery layer. The problem was not just a missing patch; the agent needed a way to ask, before acting and after failures, **what known pattern applies here?**

## What changed

- Added `skill_library.py` with static reusable skill cards.
- Added callable tools:
  - `skill_find` — find relevant skill cards/patterns for a goal or error.
  - `skill_get` — retrieve a full skill card by id.
  - `skill_propose` — store a proposed reusable skill as a typed KG `skill` node.
- The controller now injects `skill_suggestions` into every prompt.
- After an error, `skill_suggestions` are based on the latest stderr/tool failure.
- Program run context is compacted before prompting, so old stdout/stderr no longer bloats the next model call.
- Added a visible **Skill Finder / Patterns** panel in the UI.

## Why this matters

The v12 biopsy showed a known pattern:

```text
python_run cwd = workspace/
script tried plt.savefig('workspace/inscribed_polygon_analysis.png')
=> FileNotFoundError because it created workspace/workspace/...
```

That should not require a hand-coded special patch. It should be solved by retrieving the reusable skill card:

```text
python_workspace_paths
```

which tells the agent to save to `inscribed_polygon_analysis.png` or create a real subfolder first.

## Static skill cards currently included

- `python_workspace_paths`
- `matplotlib_headless_plot`
- `geometry_inscribed_polygon_circle`
- `atomic_code_loop`
- `large_payload_avoidance`
- `runtime_dependency_probe`

## Learned skills

The agent can propose new skills with `skill_propose`. These are stored as typed KG `skill` nodes. They are pattern knowledge only; they do not grant new external powers.

New external powers should still require human review and code changes.
