from __future__ import annotations

import json
import os
import traceback
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request, session, stream_with_context

import storage
import kg_core
import skill_library
from agent_core import AgentRunConfig, compact_event_for_stream, compact_payload_for_api, direct_tool_call, run_agent, run_agent_events
from llm_clients import LLMConfig
from tools import TOOL_REGISTRY, tool_specs_for_prompt, run_tool

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env.local")
load_dotenv(ROOT / ".env")

APP_VERSION = "SoulAgentOS v13-skill-finder"

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "local-dev-secret-change-me")

MODEL_PRESETS: List[Dict[str, str]] = [
    {"provider": "openai", "label": "OpenAI GPT-5.5", "model": "gpt-5.5"},
    {"provider": "openai", "label": "OpenAI GPT-4.1", "model": "gpt-4.1"},
    {"provider": "anthropic", "label": "Claude Haiku 4.5 — fast / inexpensive", "model": "claude-haiku-4-5"},
    {"provider": "anthropic", "label": "Claude Haiku 4.5 pinned snapshot", "model": "claude-haiku-4-5-20251001"},
    {"provider": "anthropic", "label": "Claude Sonnet 4.6 — balanced", "model": "claude-sonnet-4-6"},
    {"provider": "anthropic", "label": "Claude Opus 4.8 — strongest", "model": "claude-opus-4-8"},
    {"provider": "ollama", "label": "Ollama Llama 3.1 8B", "model": "llama3.1:8b"},
    {"provider": "ollama", "label": "Ollama GPT-OSS 20B", "model": "gpt-oss:20b"},
    {"provider": "openai_compatible", "label": "OpenAI-compatible local model", "model": "local-model"},
]

DEFAULT_MODELS = {
    "openai": os.getenv("OPENAI_MODEL", "gpt-5.5"),
    "anthropic": os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5"),
    "ollama": os.getenv("OLLAMA_MODEL", "llama3.1:8b"),
    "openai_compatible": os.getenv("LOCAL_MODEL", "local-model"),
}


def _default_model(provider: str) -> str:
    return DEFAULT_MODELS.get(provider, "gpt-5.5")


def _settings_from_request(payload: Dict[str, Any]) -> LLMConfig:
    provider = (payload.get("provider") or session.get("provider") or "openai").strip()
    model = (payload.get("model") or session.get("model") or _default_model(provider)).strip()
    api_key = payload.get("api_key") or session.get(f"{provider}_api_key") or ""
    base_url = payload.get("base_url") or session.get("base_url") or ""
    temperature = float(payload.get("temperature", 0.2) or 0.2)

    # Store only in Flask session unless user explicitly saves locally.
    session["provider"] = provider
    session["model"] = model
    session["base_url"] = base_url
    if api_key:
        session[f"{provider}_api_key"] = api_key

    if payload.get("save_key") and api_key:
        if provider == "openai":
            env_name, model_env_name = "OPENAI_API_KEY", "OPENAI_MODEL"
        elif provider == "anthropic":
            env_name, model_env_name = "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL"
        elif provider == "ollama":
            env_name, model_env_name = "OLLAMA_API_KEY", "OLLAMA_MODEL"
        else:
            env_name, model_env_name = "LOCAL_API_KEY", "LOCAL_MODEL"
        lines: List[str] = []
        env_path = ROOT / ".env.local"
        if env_path.exists():
            lines = env_path.read_text(encoding="utf-8").splitlines()
            lines = [line for line in lines if not line.startswith(env_name + "=") and not line.startswith(model_env_name + "=")]
        lines.append(f"{env_name}={api_key}")
        lines.append(f"{model_env_name}={model}")
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return LLMConfig(provider=provider, model=model, api_key=api_key, base_url=base_url, temperature=temperature)


def _profile_from_payload(payload: Dict[str, Any], persist: bool = False) -> Dict[str, str]:
    saved = storage.get_profile()
    user_name = (payload.get("user_name") or session.get("user_name") or saved.get("user_name") or "User").strip()
    agent_name = (payload.get("agent_name") or session.get("agent_name") or saved.get("agent_name") or "SoulAgent").strip()
    session["user_name"] = user_name
    session["agent_name"] = agent_name
    profile = {"user_name": user_name, "agent_name": agent_name}
    if persist:
        profile = storage.save_profile(user_name=user_name, agent_name=agent_name)
    return profile


def _profile_for_state() -> Dict[str, str]:
    saved = storage.get_profile()
    session.setdefault("user_name", saved.get("user_name", "User"))
    session.setdefault("agent_name", saved.get("agent_name", "SoulAgent"))
    return {"user_name": session.get("user_name") or saved.get("user_name") or "User", "agent_name": session.get("agent_name") or saved.get("agent_name") or "SoulAgent"}



def _compact_for_api(obj: Any) -> Any:
    """Use the same stream compactor recursively for API/biopsy payloads."""
    return compact_payload_for_api(obj)


def _ndjson(event: Dict[str, Any]) -> str:
    return json.dumps(compact_event_for_stream(event), ensure_ascii=False, default=str) + "\n"



def _heartbeat_mission(mode: str = "work", user_note: str = "") -> str:
    mode = (mode or "work").strip() or "work"
    user_note = (user_note or "").strip()
    note_block = f"\nUser note for this pulse:\n{user_note}\n" if user_note else ""
    return f"""GOAL PULSE — mode: {mode}

This is a local long-horizon work pulse. It is not a courtroom deposition. It is a small garage-lab tick.
{note_block}
Prime directive:
- Find the highest-priority open USER goal or USER todo and move it forward.
- Agent self-goals are allowed, but they are background instincts. They do not get the pulse while user work is waiting.
- Do not spend the pulse marking agent goals reviewed unless there are no open user goals and no open todos.

What to do by mode:
- work: produce or modify a concrete artifact in workspace/ when possible, then mark the relevant todo done or add the next todo.
- advance: move the next open user todo forward with a real action. Workspace file writes are allowed for coding/artifact tasks.
- cleanup: organize memory/goals/todos only after checking whether an open user task needs a simple next step.
- reflect: summarize status without changing files, unless a tiny todo/goal update is obviously useful.

Operating style:
1. Pick one active user goal/todo. Name it briefly in the final answer.
2. Take useful local actions for up to the configured max steps. You may use multiple tool calls in one pulse.
3. For low-stakes local project work, do not ask permission before writing files inside workspace/.
4. If you write code, prefer a complete runnable file plus a small README or usage note.
5. If a todo is actually completed, mark it done. If more remains, add the next concrete todo.
6. Keep agent goals alive, but subordinate. Your own goals should make you more useful, not slower.
7. End with: what changed, what file/todo/goal was touched, and the next obvious step.

Avoid:
- repetitive alignment sermons
- marking internal goals reviewed while user todos are open
- asking for confirmation for ordinary workspace edits
- final answers that merely say you reviewed things
"""

def _biopsy_payload(run: Dict[str, Any] | None = None) -> Dict[str, Any]:
    profile = storage.get_profile()
    provider = session.get("provider", "openai")
    model = session.get("model", _default_model(provider))
    payload = {
        "app_version": APP_VERSION,
        "profile": profile,
        "settings_without_api_key": {
            "provider": provider,
            "model": model,
            "base_url": session.get("base_url", ""),
        },
        "latest_run": run if run is not None else storage.latest_run(),
        "soul_md": storage.read_soul(),
        "open_todos": storage.list_todos("open", limit=100),
        "goals": storage.list_goals(limit=200),
        "heartbeat_settings": storage.get_heartbeat_settings(),
        "recent_heartbeats": storage.list_heartbeats(limit=20),
        "program_runs": storage.list_program_runs(limit=50),
        "workspace": run_tool("workspace_list", {"path": "."}),
        "kg": storage.search_kg("", limit=100),
        "kg_v2": kg_core.inspect(limit=200),
        "notes": storage.list_notes(limit=50),
        "skills": tool_specs_for_prompt(),
        "skill_cards": skill_library.find_skills(query="python plot graph debug file path", limit=20),
        "warning": "Biopsy is compacted for transport: large prompts/code/stdout are represented by preview + char count + hash. API keys are never included.",
    }
    return _compact_for_api(payload)


@app.before_request
def _ensure_db() -> None:
    storage.init_db()


@app.route("/")
def index():
    return render_template("index.html")


@app.get("/api/state")
def api_state():
    provider = session.get("provider", "openai")
    model = session.get("model", _default_model(provider))
    base_url = session.get("base_url", "")
    profile = _profile_for_state()
    return jsonify({
        "app_version": APP_VERSION,
        "soul_md": storage.read_soul(),
        "todos": storage.list_todos(limit=100),
        "goals": storage.list_goals(limit=200),
        "heartbeats": storage.list_heartbeats(limit=20),
        "heartbeat_settings": storage.get_heartbeat_settings(),
        "kg": storage.search_kg("", limit=100),
        "kg_v2": kg_core.inspect(limit=200),
        "notes": storage.list_notes(limit=25),
        "runs": storage.list_runs(limit=10),
        "program_runs": storage.list_program_runs(limit=20),
        "workspace": run_tool("workspace_list", {"path": "."}),
        "skills": tool_specs_for_prompt(),
        "skill_cards": skill_library.find_skills(query="python plot graph debug file path", limit=20),
        "model_presets": MODEL_PRESETS,
        "profile": profile,
        "settings": {"provider": provider, "model": model, "base_url": base_url},
    })


@app.post("/api/profile")
def api_profile_update():
    payload = request.get_json(force=True) or {}
    profile = storage.save_profile(
        user_name=payload.get("user_name", "User"),
        agent_name=payload.get("agent_name", "SoulAgent"),
    )
    session["user_name"] = profile["user_name"]
    session["agent_name"] = profile["agent_name"]
    return jsonify({"ok": True, "profile": profile})


@app.post("/api/run")
def api_run():
    payload = request.get_json(force=True) or {}
    goal = (payload.get("goal") or "").strip()
    if not goal:
        return jsonify({"ok": False, "error": "Goal is required."}), 400
    llm = _settings_from_request(payload)
    profile = _profile_from_payload(payload, persist=True)
    max_steps = int(payload.get("max_steps", 6) or 6)
    result = run_agent(goal, AgentRunConfig(
        llm=llm,
        max_steps=max_steps,
        user_name=profile["user_name"],
        agent_name=profile["agent_name"],
    ))
    return jsonify(_compact_for_api(result))


@app.post("/api/run_stream")
def api_run_stream():
    payload = request.get_json(force=True) or {}
    goal = (payload.get("goal") or "").strip()
    if not goal:
        return jsonify({"ok": False, "error": "Goal is required."}), 400
    llm = _settings_from_request(payload)
    profile = _profile_from_payload(payload, persist=True)
    max_steps = int(payload.get("max_steps", 6) or 6)
    cfg = AgentRunConfig(
        llm=llm,
        max_steps=max_steps,
        user_name=profile["user_name"],
        agent_name=profile["agent_name"],
    )

    def generate():
        try:
            for event in run_agent_events(goal, cfg):
                yield _ndjson(event)
        except Exception as e:
            yield _ndjson({"type": "server_error", "final": f"Server streaming error: {type(e).__name__}: {e}", "trace": traceback.format_exc(limit=8)})

    return Response(stream_with_context(generate()), mimetype="application/x-ndjson")



@app.post("/api/heartbeat_stream")
def api_heartbeat_stream():
    payload = request.get_json(force=True) or {}
    llm = _settings_from_request(payload)
    profile = _profile_from_payload(payload, persist=True)
    settings = storage.get_heartbeat_settings()
    mode = payload.get("mode") or settings.get("mode", "work")
    max_steps = int(payload.get("max_steps") or settings.get("max_steps") or 4)
    mission = _heartbeat_mission(mode=mode, user_note=payload.get("heartbeat_note", ""))
    cfg = AgentRunConfig(
        llm=llm,
        max_steps=max(1, min(12, max_steps)),
        user_name=profile["user_name"],
        agent_name=profile["agent_name"],
    )

    def generate():
        events: List[Dict[str, Any]] = []
        final = ""
        run_id = None
        try:
            yield _ndjson({"type": "heartbeat_start", "mode": mode, "mission": mission})
            for event in run_agent_events(mission, cfg):
                events.append(event)
                if event.get("final"):
                    final = event.get("final", "")
                if isinstance(event.get("run"), dict):
                    run_id = event["run"].get("id")
                yield _ndjson(event)
            saved = storage.save_heartbeat(mode=mode, mission=mission, final=final, run_id=run_id, transcript=events)
            yield _ndjson({"type": "heartbeat_saved", "heartbeat": saved, "final": final, "ok": True})
        except Exception as e:
            yield _ndjson({"type": "server_error", "final": f"Heartbeat streaming error: {type(e).__name__}: {e}", "trace": traceback.format_exc(limit=8)})

    return Response(stream_with_context(generate()), mimetype="application/x-ndjson")


@app.post("/api/heartbeat_settings")
def api_heartbeat_settings():
    payload = request.get_json(force=True) or {}
    settings = storage.save_heartbeat_settings(
        enabled=bool(payload.get("enabled", False)),
        interval_minutes=int(payload.get("interval_minutes", 15) or 15),
        max_steps=int(payload.get("max_steps", 4) or 4),
        mode=payload.get("mode", "work"),
    )
    return jsonify({"ok": True, "settings": settings})


@app.post("/api/goal")
def api_goal_add():
    payload = request.get_json(force=True) or {}
    goal = storage.add_goal(
        title=payload.get("title", "Untitled goal"),
        body=payload.get("body", ""),
        owner=payload.get("owner", "user"),
        horizon=payload.get("horizon", "short"),
        priority=payload.get("priority", "normal"),
        due=payload.get("due", ""),
        status=payload.get("status", "open"),
        source=payload.get("source", "manual"),
    )
    return jsonify({"ok": True, "goal": goal})


@app.post("/api/goal_update")
def api_goal_update():
    payload = request.get_json(force=True) or {}
    if not payload.get("id"):
        return jsonify({"ok": False, "error": "Goal id is required."}), 400
    goal = storage.update_goal(
        id=int(payload["id"]),
        owner=payload.get("owner"),
        horizon=payload.get("horizon"),
        title=payload.get("title"),
        body=payload.get("body"),
        status=payload.get("status"),
        priority=payload.get("priority"),
        due=payload.get("due"),
        alignment=payload.get("alignment"),
        source=payload.get("source"),
        mark_reviewed=bool(payload.get("mark_reviewed", False)),
    )
    return jsonify({"ok": True, "goal": goal})


@app.get("/api/biopsy/latest")
def api_biopsy_latest():
    return jsonify(_biopsy_payload())


@app.get("/api/biopsy/<int:run_id>")
def api_biopsy_run(run_id: int):
    run = storage.get_run(run_id)
    if not run:
        return jsonify({"ok": False, "error": "Run not found."}), 404
    return jsonify(_biopsy_payload(run))



@app.get("/api/workspace")
def api_workspace():
    path = request.args.get("path", ".")
    return jsonify({"ok": True, "files": run_tool("workspace_list", {"path": path}), "program_runs": storage.list_program_runs(limit=20)})


@app.get("/api/workspace_file")
def api_workspace_file_get():
    path = request.args.get("path", "")
    if not path:
        return jsonify({"ok": False, "error": "path is required"}), 400
    return jsonify({"ok": True, "file": run_tool("file_read", {"path": path, "max_chars": 200000})})


@app.post("/api/workspace_file")
def api_workspace_file_save():
    payload = request.get_json(force=True) or {}
    path = payload.get("path") or ""
    content = payload.get("content") or ""
    if not path:
        return jsonify({"ok": False, "error": "path is required"}), 400
    return jsonify({"ok": True, "file": run_tool("file_write", {"path": path, "content": content})})


@app.post("/api/run_program")
def api_run_program():
    payload = request.get_json(force=True) or {}
    path = payload.get("path") or ""
    timeout_seconds = int(payload.get("timeout_seconds") or 10)
    if not path:
        return jsonify({"ok": False, "error": "path is required"}), 400
    result = run_tool("python_run", {"path": path, "timeout_seconds": timeout_seconds})
    return jsonify({"ok": True, "result": result, "program_runs": storage.list_program_runs(limit=20)})


@app.post("/api/auto_debug_stream")
def api_auto_debug_stream():
    payload = request.get_json(force=True) or {}
    path = (payload.get("path") or "").strip()
    if not path:
        return jsonify({"ok": False, "error": "path is required"}), 400
    llm = _settings_from_request(payload)
    profile = _profile_from_payload(payload, persist=True)
    max_steps = int(payload.get("max_steps", 6) or 6)
    cfg = AgentRunConfig(
        llm=llm,
        max_steps=max(1, min(12, max_steps)),
        user_name=profile["user_name"],
        agent_name=profile["agent_name"],
    )

    def generate():
        try:
            yield _ndjson({"type": "autofix_start", "path": path})
            first_run = run_tool("python_run", {"path": path, "timeout_seconds": int(payload.get("timeout_seconds") or 10)})
            yield _ndjson({"type": "program_run_result", "path": path, "result": first_run})
            if first_run.get("ok"):
                final = f"Program already runs cleanly: {path}"
                yield _ndjson({"type": "final", "ok": True, "final": final})
                return
            current_file = run_tool("file_read", {"path": path, "max_chars": 50000})
            mission = f"""AUTO-FIX PROGRAM

Workspace file: {path}

Goal:
- Fix this Python program until it runs successfully.
- Use file_write to patch the file.
- Use python_run after each patch.
- Continue until stdout/stderr show success or max steps are reached.
- Be direct. Do not merely explain the fix.

Current failing run result:
{json.dumps(first_run, ensure_ascii=False, indent=2, default=str)}

Current file content:
{current_file.get('content','')}
"""
            for event in run_agent_events(mission, cfg):
                yield _ndjson(event)
        except Exception as e:
            yield _ndjson({"type": "server_error", "final": f"Auto-fix streaming error: {type(e).__name__}: {e}", "trace": traceback.format_exc(limit=8)})

    return Response(stream_with_context(generate()), mimetype="application/x-ndjson")



@app.get("/api/kg_v2")
def api_kg_v2():
    q = request.args.get("q", "")
    kind = request.args.get("kind", "")
    status = request.args.get("status", "")
    if q or kind or status:
        return jsonify({"ok": True, "mode": "retrieve", "results": kg_core.retrieve(query=q, kind=kind, status=status, limit=80), "inspect": kg_core.inspect(limit=200)})
    return jsonify({"ok": True, "mode": "inspect", "inspect": kg_core.inspect(limit=300)})


@app.post("/api/kg_node")
def api_kg_node_add():
    payload = request.get_json(force=True) or {}
    node = kg_core.add_node(
        kind=payload.get("kind", "fact"),
        title=payload.get("title", "Untitled node"),
        body=payload.get("body", ""),
        status=payload.get("status", "proto"),
        source=payload.get("source", "manual"),
        confidence=float(payload.get("confidence", 0.7) or 0.7),
        hp=float(payload.get("hp", 1.0) or 1.0),
        critic_score=float(payload.get("critic_score", 0.5) or 0.5),
        tags=payload.get("tags", ""),
        precondition=payload.get("precondition") or {},
        meta=payload.get("meta") or {},
        persists=bool(payload.get("persists", False)),
    )
    return jsonify({"ok": True, "node": node})


@app.post("/api/kg_edge")
def api_kg_edge_add():
    payload = request.get_json(force=True) or {}
    edge = kg_core.add_edge(
        src=payload.get("src", ""),
        dst=payload.get("dst", ""),
        channel=payload.get("channel", "related"),
        weight=float(payload.get("weight", 1.0) or 1.0),
        source=payload.get("source", "manual"),
    )
    return jsonify({"ok": True, "edge": edge})


@app.post("/api/kg_evidence")
def api_kg_evidence_add():
    payload = request.get_json(force=True) or {}
    node = kg_core.add_evidence(
        node_id=payload.get("node_id", ""),
        polarity=payload.get("polarity", "for"),
        evidence=payload.get("evidence", ""),
        source=payload.get("source", "manual"),
    )
    return jsonify({"ok": True, "node": node})


@app.post("/api/kg_lifecycle")
def api_kg_lifecycle():
    return jsonify({"ok": True, "result": kg_core.lifecycle_tick(), "inspect": kg_core.inspect(limit=200)})


@app.post("/api/kg_import_legacy")
def api_kg_import_legacy():
    return jsonify({"ok": True, "result": kg_core.import_simple_triples(limit=500), "inspect": kg_core.inspect(limit=200)})


@app.post("/api/soul")
def api_soul_update():
    payload = request.get_json(force=True) or {}
    text = payload.get("soul_md", "")
    storage.write_soul(text)
    return jsonify({"ok": True, "soul_md": storage.read_soul()})


@app.post("/api/tool")
def api_tool():
    payload = request.get_json(force=True) or {}
    tool = payload.get("tool")
    args = payload.get("args") or {}
    if tool not in TOOL_REGISTRY:
        return jsonify({"ok": False, "error": f"Unknown tool: {tool}"}), 400
    return jsonify(direct_tool_call(tool, args))


@app.get("/api/search")
def api_search():
    q = request.args.get("q", "")
    return jsonify({
        "kg": storage.search_kg(q, limit=50),
        "memory": storage.search_notes_and_episodes(q, limit=20),
    })


@app.post("/api/todo")
def api_todo_add():
    payload = request.get_json(force=True) or {}
    return jsonify({"ok": True, "todo": storage.add_todo(payload.get("task", ""), payload.get("priority", "normal"), payload.get("due", ""))})


@app.post("/api/kg")
def api_kg_add():
    payload = request.get_json(force=True) or {}
    return jsonify({"ok": True, "kg": storage.add_kg(payload.get("subject", ""), payload.get("predicate", ""), payload.get("object", ""), payload.get("source", "manual"), float(payload.get("confidence", 0.8)))})


@app.post("/api/note")
def api_note_add():
    payload = request.get_json(force=True) or {}
    return jsonify({"ok": True, "note": storage.add_note(payload.get("title", "Untitled"), payload.get("body", ""), payload.get("tags", ""))})


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5007)
