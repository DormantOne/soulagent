# GitHub Download and Install Instructions

This file is for people who do **not** use Git.

Use the GitHub **Release zip**, not `git clone`.

## Download

1. Open the SoulAgentOS GitHub page.
2. Click **Releases** on the right side of the page.
3. Open the newest release, for example **SoulAgentOS v0.13 — Skill Finder**.
4. Scroll to **Assets**.
5. Click **SoulAgentOS-v0.13.zip**.
6. Save the zip to your Desktop or Downloads folder.
7. Unzip it.
8. Open the unzipped folder named **SoulAgentOS-v0.13**.

Inside the folder you should see:

```text
README.md
run.py
app.py
agent_core.py
tools.py
storage.py
llm_clients.py
requirements.txt
soul.md
IMPLEMENTATION_GUIDE.html
workspace/
data/
```

## Mac install

Open Terminal in the unzipped **SoulAgentOS-v0.13** folder, then run:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

Then open:

```text
http://127.0.0.1:5007
```

## Windows install

Open PowerShell in the unzipped **SoulAgentOS-v0.13** folder, then run:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run.py
```

If PowerShell blocks activation, run this first:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.venv\Scripts\Activate.ps1
```

Then open:

```text
http://127.0.0.1:5007
```

## First test

In the app:

1. Enter your display name.
2. Choose provider and model.
3. Paste your API key.
4. Try:

```text
Write a Python program that prints the first 20 prime numbers. Run it and show stdout.
```

Expected behavior:

- the agent writes a `.py` file inside `workspace/`
- the Lab Bench shows the file
- the program runs
- stdout appears in Program Output
- if it crashes, the agent can inspect stderr and patch the file

## Safety notes

- Do not expose this app directly to the public internet.
- Do not treat `python_run` as a hardened sandbox.
- Do not commit `.env.local`, API keys, private databases, or biopsy files.
- Email or other external-action skills should be draft-first and approval-gated.

## Debugging

If something fails:

1. Click **Biopsy**.
2. Save or copy the JSON.
3. Paste the JSON into ChatGPT with: “Please diagnose this SoulAgentOS failure.”

Biopsy exports do not include the API key field.
