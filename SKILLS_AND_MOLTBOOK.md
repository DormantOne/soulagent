# Skills, Plugins, and Moltbook Notes

SoulAgentOS can grow by adding new skills. A skill is a local Python function with a JSON schema that tells the agent how to call it.

## Current skill pattern

Skills live in `tools.py`.

Each skill has:

1. A Python function that does the real work.
2. A schema entry exposed to the LLM.
3. A dispatcher entry that maps the schema name to the function.
4. A small visible result returned as JSON.

The agent does not magically access the computer. It only gets access to skills you explicitly expose.

---

## Good starter skills

Useful next skills:

- `http_post_json` — send JSON to a trusted API endpoint
- `read_rss_feed` — read feeds without full browser automation
- `github_create_issue` — open an issue in a repo using a token
- `github_upload_gist` — create a gist from a workspace file
- `workspace_search` — search local workspace files
- `csv_analyze` — read a CSV and summarize columns
- `html_preview` — render a workspace HTML file in an iframe
- `scheduled_goal_pulse` — controlled recurring local pulse while app is running

---

## Skill safety levels

Recommended levels:

### Level 0: read-only local
Examples: list files, read files, search memory.

### Level 1: local reversible write
Examples: write workspace file, edit todo, add memory note.

### Level 2: local execution
Examples: run Python in workspace. Useful but not a hardened sandbox.

### Level 3: external write
Examples: post to GitHub, post to Moltbook, send email, create calendar event.
These should require clear user configuration and preferably visible approval.

### Level 4: money/credentials/system operations
Avoid in this starter app unless heavily sandboxed and reviewed.

---

## Can it go on Moltbook?

Architecturally, yes, if Moltbook exposes an API or web interface that permits agent posting.

The clean way is to add a Moltbook skill, not let the agent freestyle with raw browser control.

Possible minimal skill design:

```json
{
  "name": "moltbook_post",
  "description": "Post a short message to a configured Moltbook submolt using the user's Moltbook token.",
  "parameters": {
    "type": "object",
    "properties": {
      "submolt": {"type": "string"},
      "title": {"type": "string"},
      "body": {"type": "string"}
    },
    "required": ["submolt", "title", "body"]
  },
  "human_approval": true
}
```

Suggested first Moltbook workflow:

1. User configures Moltbook token in `.env`, not in source code.
2. Agent drafts a post.
3. UI shows the draft.
4. User clicks approve.
5. Skill posts to Moltbook.
6. Biopsy records the post metadata but not the token.

Recommended Moltbook guardrails:

- no autonomous posting by default
- rate limits
- submolt allowlist
- max post length
- no private data
- no credential echoing
- no automatic following/upvoting until reviewed

---

## Can it write its own skills?

It can help draft new skills now, but it should not automatically install arbitrary new skills without review.

A safer pattern:

1. Agent writes a proposed skill file into `workspace/proposed_skills/`.
2. Agent writes a schema and tests.
3. Lab Bench runs the tests.
4. Human reviews the code.
5. Human copies it into `tools.py` or clicks a future controlled "Install Skill" button.

Future feature idea:

- `propose_skill`
- `test_skill`
- `install_skill_after_approval`

Do not let a model silently grant itself new external powers.
