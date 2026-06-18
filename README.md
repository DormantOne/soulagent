# SoulAgentOS

## v0.13 — Skill Finder release

SoulAgentOS v0.13 includes the typed KG/fovea, generic atomic controller, stream hygiene, and the Skill Finder / pattern fovea. This is the zip intended for GitHub Releases → Assets.

A local Flask GUI for a small but expandable agent: API-key input, `Soul.md`, SQLite memory, a knowledge graph, goals, a goal pulse, todos, and built-in skills.

This is meant to be a **working starter agent operating system**, not a magic autonomous creature. It gives an LLM a controlled loop:

1. Read `Soul.md` and relevant memory.
2. Decide one JSON action.
3. Execute exactly one approved local skill.
4. Feed the tool result back into the next step.
5. Stop with a final answer, optional memory writes, KG triples, and todos.

No shell skill is included. File access is restricted to `workspace/`. v7/v9 add a visible Lab Bench: browse workspace files, inspect/edit code, run Python programs, capture stdout/stderr, and invoke an auto-fix loop.

---

## GitHub / first-time download

For non-developer install instructions, start with:

- [`GITHUB_DOWNLOAD_INSTRUCTIONS.md`](GITHUB_DOWNLOAD_INSTRUCTIONS.md)
- [`GITHUB_DOWNLOAD_INSTRUCTIONS.html`](GITHUB_DOWNLOAD_INSTRUCTIONS.html)
- [`IMPLEMENTATION_GUIDE.html`](IMPLEMENTATION_GUIDE.html)

For publishing this project as a GitHub release zip, see:

- [`GITHUB_RELEASE_CHECKLIST.md`](GITHUB_RELEASE_CHECKLIST.md)

For adding new skills or connecting to agent platforms such as Moltbook, see:

- [`SKILLS_AND_MOLTBOOK.md`](SKILLS_AND_MOLTBOOK.md)

---

## Features

- **Flask GUI** at `http://127.0.0.1:5007`
- **Provider support**
  - OpenAI Responses API
  - Anthropic Claude Messages API
  - Ollama local `/api/chat`
  - Generic OpenAI-compatible `/chat/completions`
- **Shareable identity panel**: each user can enter their own display name and rename the agent. Identity now autosaves to SQLite and browser localStorage.
- **Model presets** including Claude Haiku 4.5 alias and pinned snapshot.
- **Soul.md** editable in the GUI
- **SQLite memory**
  - episodic run history
  - notes
  - todo queue
  - user / agent / shared goals
  - heartbeat logs
  - knowledge graph triples
  - skill-call logs
- **Skills / tools**
  - calculator
  - memory add/search
  - KG add/search
  - todo add/list/update
  - goal add/list/update
  - read/append Soul.md
  - read/write/list files inside `workspace/`
  - run Python `.py` files inside `workspace/` with stdout/stderr capture
  - fetch URL text
  - build a prompt for another LLM to make JSON command plans
- **Live agent trace** shows model requests, raw model responses, parsed JSON, tool calls, tool results, and automatic Python run results as they happen.
- **Biopsy button** downloads/copies a diagnostic JSON bundle for debugging failures with ChatGPT.
- **Program Lab Bench** with file selector, code viewer/editor, Run Program button, clear stdout/stderr output screen, recent run history, and Run + Auto-Fix button.
- **Goal pulse / long-horizon mode**: a visible loop that advances open user goals/todos first, then does agent housekeeping only when user work is clear. When it writes a `.py` file, the app auto-runs it and captures stdout/stderr.
- **Goal system**: separate user goals, agent self-goals, and shared goals, with short/long horizon and alignment notes.

---

## Install

```bash
cd soul_agent_os
python -m venv .venv

# macOS / Linux
source .venv/bin/activate

# Windows PowerShell
# .venv\Scripts\Activate.ps1

pip install -r requirements.txt
python run.py
```

Open:

```text
http://127.0.0.1:5007
```


## Program Lab Bench

The Lab Bench is the main debugging surface. It is intentionally separate from the raw agent trace.

Use it like this:

1. Let the agent write a file into `workspace/`, or place a `.py` file there yourself.
2. Select the file in **Program Lab Bench**.
3. Press **Run Program** to execute it with the app's `python_run` skill.
4. Inspect stdout/stderr in the **Program Output** screen.
5. Press **Run + Auto-Fix** to have the agent run the file, read the error, patch the file with `file_write`, run it again, and continue until it succeeds or hits max steps.

This is a low-stakes local runner, not a hardened sandbox. It runs Python files inside `workspace/` with a sparse environment so API keys are not handed to generated scripts by default.

## Context compaction and real tool-call fix

v9 fixes the `PLACEHOLDER` crash in compact context clipping. It also treats provider-emitted `<function_calls>...</function_calls>` blocks as real tool calls to execute before trusting any later final JSON. This prevents the nasty failure mode where a model says it wrote/ran code, but the workspace and program run table remain empty.

v7 also fixes a subtle long-horizon failure: earlier versions could feed full previous heartbeat prompts/transcripts back into later heartbeat prompts. That made prompt size grow and could cause model/network errors. The model now sees compact heartbeat and episode summaries, while the full details remain available in Biopsy.

---

## API keys

You can paste an API key into the GUI. By default it is stored only in your Flask session.

Optional: check **Save key and selected model into .env.local** to write them locally. Do not commit `.env.local`.

You may also copy `.env.example` to `.env.local` and fill in keys manually:

```bash
cp .env.example .env.local
```

---

## Anthropic Haiku

Choose provider **Anthropic Claude Messages API**, then choose **Claude Haiku 4.5 — fast / inexpensive**. The editable model field will become:

```text
claude-haiku-4-5
```

You can also choose the pinned snapshot:

```text
claude-haiku-4-5-20251001
```

The editable model box is intentionally left open, so you can paste a newer Anthropic model ID later without changing code.

---

## Shareable identity

Use **Your display name** and **Agent name** in the settings panel. The values autosave to browser `localStorage`, save to the local SQLite `kv` table, and are included in the agent prompt. This makes the app shareable: the next user can rename both themselves and the agent from the GUI.

If you refresh the page or restart Flask, the identity should remain. The app also saves identity automatically before every agent run.

---


## Goals and goal pulse

SoulAgentOS v6 changes the old heartbeat from a cautious review loop into a **goal pulse**. The point is progress. If a user goal or todo is open, the pulse should move that forward before touching agent housekeeping.

### Goal types

- **User goals**: what the user wants. These outrank everything else.
- **Agent goals**: the agent’s own background instincts: preserve useful memory, keep the workspace organized, notice next steps, and remain debuggable. These are real but secondary.
- **Shared goals**: overlap between user aims and useful agent maintenance.

Goals are strategic. Todos are concrete next actions.

### Goal pulse

The pulse is not a hidden daemon. It runs from the browser while the app is open. In default **work** mode, each pulse should:

1. Pick the highest-priority open user goal or todo.
2. Do useful local work, usually by writing/modifying a file in `workspace/`.
3. Use multiple tool calls up to the configured max steps when useful.
4. Mark completed todos done.
5. Add the next obvious todo if more remains.
6. Save a visible pulse log and include it in Biopsy.

It should **not** waste a pulse marking internal goals reviewed while user work is waiting.

Use **Pulse Once Now** for testing. Use **Start Pulse** to repeat at the selected interval while the tab remains open. Closing the tab stops the browser timer, although the enabled setting is remembered.

### Python runner

The `python_run` skill can run a `.py` file inside `workspace/` and return stdout/stderr. It uses no shell, a timeout, and a sparse environment. It is still local code execution, not a hardened security sandbox. Use it for your own low-stakes lab work.

## Suggested first test

Use OpenAI or Anthropic with a key, then ask:

```text
Add a todo to build the first custom skill, save a KG triple saying SoulAgentOS has a SQLite knowledge graph, and write a short project note about why this app exists.
```

You should see a live step-by-step trace while the run is happening: context gathering, model request, raw model response, parsed JSON, tool call, tool result, final answer, todo, KG triple, and saved note.

---

## Live trace and Biopsy

The v6 UI makes failures visible instead of hiding them until the end. During a run you will see:

1. Step start and memory/context summary.
2. Model request size.
3. Raw model response.
4. Parsed JSON action/final object.
5. Tool name and arguments.
6. Tool result or tool error.
7. Final answer or stop/error state.

Use **Biopsy** to download a JSON file, or **Copy Biopsy** to copy it to the clipboard. Paste that JSON back into ChatGPT and ask for a diagnosis. The biopsy bundle includes prompts, raw model output, parsed JSON, tool args/results, recent memory, Soul.md, KG, goals, heartbeat history, todos, and notes. It does **not** include the API key typed into the password field.

## How the agent loop works

The app does **not** give the model uncontrolled access to your computer.

The model is prompted to produce JSON in one of two forms.

Tool step:

```json
{
  "thought": "Brief reason for the next action.",
  "action": {"tool": "todo_add", "args": {"task": "Build custom skill", "priority": "normal"}}
}
```

Final answer:

```json
{
  "final": "Done.",
  "memory_writes": [{"title": "Project note", "body": "...", "tags": "project"}],
  "kg_triples": [{"subject": "SoulAgentOS", "predicate": "has", "object": "SQLite knowledge graph"}],
  "todos": [{"task": "Add calendar skill", "priority": "normal", "due": ""}]
}
```

This protocol is deliberately provider-agnostic. It works with OpenAI, Claude, Ollama, and local models even when native tool-calling APIs differ.

---

## File layout

```text
soul_agent_os/
  app.py              Flask routes
  run.py              local launcher
  agent_core.py       agent loop + JSON protocol
  llm_clients.py      OpenAI/Anthropic/Ollama/OpenAI-compatible clients
  storage.py          SQLite storage layer
  tools.py            skill registry and built-in skills
  soul.md             editable agent constitution
  data/               SQLite DB appears here
  workspace/          agent-readable/writable files only
  templates/index.html
  static/style.css
  static/app.js
```

---

## Adding a custom skill

Open `tools.py`.

1. Write a Python function.
2. Add a `Tool(...)` entry to `TOOL_REGISTRY`.
3. Restart Flask.

Example:

```python
def skill_reverse_text(text: str):
    return {"reversed": text[::-1]}

TOOL_REGISTRY["reverse_text"] = Tool(
    "reverse_text",
    "Reverse a string.",
    {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    skill_reverse_text,
)
```

The GUI will show it automatically.

---

## Safety model

This starter agent is intentionally caged.

- No shell execution.
- No arbitrary filesystem access.
- No hidden background autonomy. Heartbeat only runs while the browser tab is open, and every beat is logged.
- No hidden external actions.
- URL fetching is explicit and logged.
- All tool calls go through the Python registry.
- Irreversible actions should be implemented later with human approval gates.

Future high-risk skills should add approval screens before execution.

---

## Growth path

Good next upgrades:

1. Native tool-calling adapters for OpenAI and Claude.
2. Embedding-based vector memory.
3. Real graph visualization.
4. Skill permission levels: safe, approval-required, forbidden.
5. Background scheduler with explicit user-created jobs.
6. Workspace document ingestion.
7. Project-specific skill packs.
8. OAuth connectors only after the local safety model is stable.

---

## Troubleshooting

### Missing key

Paste a key in the GUI or set `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` in `.env.local`.

### Ollama

Start Ollama first:

```bash
ollama serve
ollama pull llama3.1:8b
```

Then choose provider `Ollama local`, model `llama3.1:8b`, base URL `http://localhost:11434`.

### Model returns invalid JSON

Lower temperature, use a stronger model, or make the task more explicit. The agent tries to recover fenced JSON, but it intentionally fails rather than guessing tool actions.

### v3 parser hardening

If a smaller or faster model returns JSON plus extra text, fenced JSON, or two JSON objects back-to-back, the agent now accepts the first valid JSON object instead of crashing with `JSONDecodeError: Extra data`. The system prompt also more strongly tells the model to return exactly one object.


---

## v4 changes

- Added `/api/run_stream` using NDJSON streaming.
- Added live visible run log.
- Added raw event trace.
- Added Biopsy and Copy Biopsy buttons.
- Added `/api/biopsy/latest` and `/api/biopsy/<run_id>`.
- Agent transcripts now save raw model responses as well as parsed JSON.
- Identity autosaves on edit and before every run.
- Identity is included more explicitly in the agent system prompt.


## v6 Goal Pulse changes

v6 changes the heartbeat from a cautious review loop into a visible **goal pulse**. The default mode is `work`, which means:

- open user goals and user todos outrank agent self-goals;
- the pulse should produce or modify an artifact when a project goal is waiting;
- workspace file writes are allowed for ordinary low-stakes local work;
- internal agent housekeeping happens only when user work is clear;
- a new `python_run` skill can run `.py` files inside `workspace/` and return stdout/stderr.

`python_run` is a lab runner, not a hardened security sandbox. It avoids shell execution and uses a sparse environment, but generated Python is still local code. Use it for your own low-stakes workspace, not untrusted code from strangers.

## v10 parser hardening

v10 fixes a v9 failure mode where a model began a `<function_calls>` block, pasted a huge generated file payload, and got truncated before closing valid JSON. Earlier v9 would then crash trying to parse the whole raw response as one JSON object. v10 detects broken function-call blocks, refuses to trust any later final claim in that response, and retries with a compact instruction: write smaller JSON, do not inline huge constants/datasets, and compute data at runtime.

## v11 generic atomic controller

v11 adds a generic control-loop improvement, not a specific patch for one failure. It forces difficult work into small observable actions, rejects overlarge tool payloads, treats parse failures as retryable observations, compacts transcript context, and blocks premature final answers for code/artifact tasks until tool evidence exists. See `GENERIC_AGENT_LOOP_V11.md`.



## v12 stream hygiene

See `STREAM_HYGIENE_V12.md`. Large prompts, code payloads, stdout/stderr, and transcripts are now compacted before browser streaming and Biopsy export. The controller still executes real tool calls, but the visible stream uses previews, counts, and hashes so the UI does not collapse under its own trace.


## v13 skill finder

SoulAgentOS v13 adds a generic skill-discovery layer instead of another one-off patch. The agent now receives a `skill_suggestions` fovea before acting and after failures, and can call `skill_find`, `skill_get`, or `skill_propose`. This lets it reuse patterns like Python workspace path hygiene, headless matplotlib plotting, inscribed-polygon formulas, atomic code loops, and large-payload avoidance. See `SKILL_FINDER_V13.md`.
