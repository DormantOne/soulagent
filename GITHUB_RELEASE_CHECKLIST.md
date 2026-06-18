# GitHub Release Checklist

Use this when publishing SoulAgentOS as a zip or release asset.

## Before upload

Confirm the package includes:

- `README.md`
- `IMPLEMENTATION_GUIDE.html`
- `GITHUB_DOWNLOAD_INSTRUCTIONS.md`
- `SKILLS_AND_MOLTBOOK.md`
- `.env.example`
- `.gitignore`
- `requirements.txt`
- app source files
- empty `data/.keep`
- empty `workspace/.keep`

Confirm the package does not include:

- `.env`
- real API keys
- `data/*.db`
- personal Biopsy JSONs
- `__pycache__/`
- `.venv/`
- user-created workspace files unless intentionally included as examples

## GitHub web upload, simple version

1. Create a new GitHub repository.
2. Upload the project files or upload the prepared zip.
3. Edit the repository description.
4. Make sure the README renders clearly.
5. Add a release named something like `v0.1.0-lab`.
6. Attach the zip as a release asset.
7. Copy the release download link and share that.

## Suggested repo description

```text
A local Flask-based AI agent lab with Soul.md, memory, goals, workspace tools, Python run/debug loop, and Biopsy exports.
```

## Suggested first release title

```text
SoulAgentOS v0.1.0 — local agent lab with Biopsy and Lab Bench
```

## Suggested release notes

```text
Initial experimental release.

Features:
- Flask GUI
- Anthropic/OpenAI/Ollama/OpenAI-compatible providers
- Soul.md personality/constitution file
- SQLite memory, notes, goals, todos, KG triples
- Program Lab Bench with stdout/stderr
- Python workspace runner
- Run + Auto-Fix loop
- Biopsy export for failure diagnosis

Warning:
This is a local experimental agent lab, not a hardened sandbox. Run only on a trusted machine and do not expose it directly to the public internet.
```
