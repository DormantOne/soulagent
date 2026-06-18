from __future__ import annotations

import ast
import json
import math
import re
import subprocess
import sys
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

import storage
import kg_core
import skill_library

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT / "workspace"
WORKSPACE.mkdir(exist_ok=True)


class ToolError(Exception):
    pass


@dataclass
class Tool:
    name: str
    description: str
    parameters: Dict[str, Any]
    func: Callable[..., Any]
    human_approval: bool = False


def _safe_path(path: str) -> Path:
    p = (WORKSPACE / path).resolve()
    if not str(p).startswith(str(WORKSPACE.resolve())):
        raise ToolError("Path escapes workspace. Use a relative path inside workspace/.")
    return p


_ALLOWED_AST = {
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Num, ast.Constant,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.USub, ast.UAdd, ast.Load, ast.Call, ast.Name, ast.Tuple,
}
_ALLOWED_NAMES = {k: getattr(math, k) for k in dir(math) if not k.startswith("_")}
_ALLOWED_NAMES.update({"abs": abs, "round": round, "min": min, "max": max})


def skill_calculator(expression: str) -> Dict[str, Any]:
    """Safe-ish math evaluator; no attributes, no imports, no filesystem."""
    tree = ast.parse(expression, mode="eval")
    for node in ast.walk(tree):
        if type(node) not in _ALLOWED_AST:
            raise ToolError(f"Disallowed expression element: {type(node).__name__}")
        if isinstance(node, ast.Call) and not isinstance(node.func, ast.Name):
            raise ToolError("Only direct math function calls are allowed.")
        if isinstance(node, ast.Name) and node.id not in _ALLOWED_NAMES:
            raise ToolError(f"Unknown name: {node.id}")
    value = eval(compile(tree, "<calculator>", "eval"), {"__builtins__": {}}, _ALLOWED_NAMES)
    return {"expression": expression, "value": value}


def skill_memory_note_add(title: str, body: str, tags: str = "") -> Dict[str, Any]:
    return storage.add_note(title, body, tags)


def skill_memory_search(query: str, limit: int = 10) -> Dict[str, Any]:
    return storage.search_notes_and_episodes(query, limit)


def skill_kg_add(subject: str, predicate: str, object: str, source: str = "agent", confidence: float = 0.7) -> Dict[str, Any]:
    return storage.add_kg(subject, predicate, object, source, confidence)


def skill_kg_search(query: str = "", limit: int = 20) -> List[Dict[str, Any]]:
    return storage.search_kg(query, limit)


def skill_todo_add(task: str, priority: str = "normal", due: str = "") -> Dict[str, Any]:
    return storage.add_todo(task, priority, due)


def skill_todo_list(status: str = "", limit: int = 50) -> List[Dict[str, Any]]:
    return storage.list_todos(status, limit)


def skill_todo_update(id: int, status: str | None = None, task: str | None = None, priority: str | None = None, due: str | None = None) -> Dict[str, Any]:
    return storage.update_todo(id, status=status, task=task, priority=priority, due=due)


def skill_goal_add(title: str, body: str = "", owner: str = "user", horizon: str = "short", priority: str = "normal", due: str = "", status: str = "open", parent_goal_id: int | None = None, alignment: Dict[str, Any] | None = None, source: str = "agent") -> Dict[str, Any]:
    return storage.add_goal(title=title, body=body, owner=owner, horizon=horizon, priority=priority, due=due, status=status, parent_goal_id=parent_goal_id, alignment=alignment, source=source)


def skill_goal_list(owner: str = "", status: str = "", horizon: str = "", limit: int = 50) -> List[Dict[str, Any]]:
    return storage.list_goals(owner=owner, status=status, horizon=horizon, limit=limit)


def skill_goal_update(id: int, owner: str | None = None, horizon: str | None = None, title: str | None = None, body: str | None = None, status: str | None = None, priority: str | None = None, due: str | None = None, parent_goal_id: int | None = None, alignment: Dict[str, Any] | None = None, source: str | None = None, mark_reviewed: bool = False) -> Dict[str, Any]:
    return storage.update_goal(id=id, owner=owner, horizon=horizon, title=title, body=body, status=status, priority=priority, due=due, parent_goal_id=parent_goal_id, alignment=alignment, source=source, mark_reviewed=mark_reviewed)


def skill_soul_read() -> Dict[str, str]:
    return {"soul_md": storage.read_soul()}


def skill_soul_append(text: str) -> Dict[str, Any]:
    current = storage.read_soul()
    addition = "\n\n" + text.strip() + "\n"
    storage.write_soul(current.rstrip() + addition)
    return {"ok": True, "appended_chars": len(addition), "soul_md": storage.read_soul()}


def skill_file_write(path: str, content: str) -> Dict[str, Any]:
    p = _safe_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return {"ok": True, "path": str(p.relative_to(WORKSPACE)), "bytes": len(content.encode("utf-8"))}


def skill_file_read(path: str, max_chars: int = 6000) -> Dict[str, Any]:
    p = _safe_path(path)
    if not p.exists():
        raise ToolError("File not found in workspace.")
    text = p.read_text(encoding="utf-8", errors="replace")
    return {"path": str(p.relative_to(WORKSPACE)), "content": text[: int(max_chars)], "truncated": len(text) > int(max_chars)}


def skill_workspace_list(path: str = ".") -> List[Dict[str, Any]]:
    p = _safe_path(path)
    if not p.exists():
        raise ToolError("Workspace path not found.")
    if p.is_file():
        return [{"name": p.name, "path": str(p.relative_to(WORKSPACE)), "type": "file", "bytes": p.stat().st_size}]
    out = []
    for child in sorted(p.iterdir()):
        out.append({
            "name": child.name,
            "path": str(child.relative_to(WORKSPACE)),
            "type": "dir" if child.is_dir() else "file",
            "bytes": child.stat().st_size if child.is_file() else None,
        })
    return out


def skill_python_run(path: str, timeout_seconds: int = 10) -> Dict[str, Any]:
    """Run a Python file in workspace/. This is a low-stakes lab runner, not a security sandbox."""
    p = _safe_path(path)
    if not p.exists() or not p.is_file():
        raise ToolError("Python file not found in workspace.")
    if p.suffix.lower() != ".py":
        raise ToolError("python_run only runs .py files inside workspace/.")
    env = {
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "MPLBACKEND": "Agg",
    }
    # Keep the environment sparse so API keys are not leaked into generated scripts by default.
    try:
        completed = subprocess.run(
            [sys.executable, str(p)],
            cwd=str(WORKSPACE),
            input="",
            text=True,
            capture_output=True,
            timeout=max(1, min(60, int(timeout_seconds or 10))),
            env=env,
        )
        result = {
            "ok": completed.returncode == 0,
            "path": str(p.relative_to(WORKSPACE)),
            "returncode": completed.returncode,
            "stdout": completed.stdout[-8000:],
            "stderr": completed.stderr[-8000:],
        }
        try:
            result["program_run"] = storage.add_program_run(result["path"], result, source="python_run")
        except Exception:
            pass
        return result
    except subprocess.TimeoutExpired as e:
        result = {
            "ok": False,
            "path": str(p.relative_to(WORKSPACE)),
            "returncode": None,
            "error": f"Timed out after {timeout_seconds} seconds",
            "stdout": (e.stdout or "")[-4000:] if isinstance(e.stdout, str) else "",
            "stderr": (e.stderr or "")[-4000:] if isinstance(e.stderr, str) else "",
        }
        try:
            result["program_run"] = storage.add_program_run(result["path"], result, source="python_run_timeout")
        except Exception:
            pass
        return result


def skill_web_fetch_url(url: str, max_chars: int = 5000) -> Dict[str, Any]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ToolError("Only http/https URLs are allowed.")
    r = requests.get(url, timeout=12, headers={"User-Agent": "SoulAgentOS/0.1"})
    r.raise_for_status()
    ctype = r.headers.get("content-type", "")
    text = r.text
    if "html" in ctype.lower() or "<html" in text[:500].lower():
        soup = BeautifulSoup(text, "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        body = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    else:
        title = ""
        body = text
    return {"url": url, "title": title, "content": body[: int(max_chars)], "truncated": len(body) > int(max_chars)}



def skill_kg_node_add(kind: str = "fact", title: str = "Untitled node", body: str = "", status: str = "proto", source: str = "agent", confidence: float = 0.7, hp: float = 1.0, critic_score: float = 0.5, tags: str = "", precondition: dict | None = None, meta: dict | None = None, persists: bool = False) -> Dict[str, Any]:
    """Add a typed KG node: fact, belief, prediction, commitment, intent, artifact, skill, goal, note, observation, hypothesis."""
    return kg_core.add_node(kind=kind, title=title, body=body, status=status, source=source, confidence=confidence, hp=hp, critic_score=critic_score, tags=tags, precondition=precondition or {}, meta=meta or {}, persists=bool(persists))


def skill_kg_edge_add(src: str, dst: str, channel: str = "related", weight: float = 1.0, source: str = "agent") -> Dict[str, Any]:
    """Add a weighted multi-channel edge between two KG v2 nodes."""
    return kg_core.add_edge(src=src, dst=dst, channel=channel, weight=weight, source=source)


def skill_kg_evidence_add(node_id: str, polarity: str = "for", evidence: str = "", source: str = "agent") -> Dict[str, Any]:
    """Attach evidence for/against a KG node and update HP/success deterministically."""
    return kg_core.add_evidence(node_id=node_id, polarity=polarity, evidence=evidence, source=source)


def skill_kg_retrieve(query: str = "", kind: str = "", status: str = "", limit: int = 8) -> List[Dict[str, Any]]:
    """Retrieve typed KG nodes using the fovea scoring system."""
    return kg_core.retrieve(query=query, kind=kind, status=status, limit=limit)


def skill_kg_fovea(query: str = "", limit: int = 8) -> Dict[str, Any]:
    """Return the narrow KG fovea that should enter the prompt for a task."""
    return kg_core.fovea(query=query, limit=limit)


def skill_kg_inspect(limit: int = 200) -> Dict[str, Any]:
    """Read-only inspection snapshot of the typed KG: nodes, edges, hubs, counts, events."""
    return kg_core.inspect(limit=limit)


def skill_kg_lifecycle_tick() -> Dict[str, Any]:
    """Run deterministic KG lifecycle: decay idle HP, promote supported proto nodes, retire weak/refuted nodes."""
    return kg_core.lifecycle_tick()


def skill_skill_find(query: str = "", error: str = "", context: str = "", limit: int = 5) -> Dict[str, Any]:
    """Find reusable local skill cards/patterns for a task or error. Use before coding and after failures."""
    return skill_library.find_skills(query=query, error=error, context=context, limit=limit)


def skill_skill_get(skill_id: str) -> Dict[str, Any]:
    """Get the full text of a reusable skill card by id."""
    card = skill_library.get_skill(skill_id)
    if not card:
        raise ToolError(f"Skill card not found: {skill_id}")
    return card


def skill_skill_propose(title: str, body: str, triggers: str = "", tools: str = "", source: str = "agent") -> Dict[str, Any]:
    """Store a proposed reusable skill as a typed KG skill node for later retrieval. Does not grant new external powers."""
    return kg_core.add_node(
        kind="skill",
        title=title,
        body=body,
        status="proto",
        source=source or "agent",
        confidence=0.75,
        hp=1.0,
        tags=", ".join(x for x in [triggers, tools] if x),
        meta={"triggers": triggers, "tools": tools, "permission": "pattern_only_no_new_external_power"},
        persists=True,
    )

def skill_make_prompt_for_llm(task: str, constraints: str = "", desired_json_schema: str = "") -> Dict[str, str]:
    prompt = f"""You are an agent planner. Convert the task into a safe, explicit JSON command plan.

Task:
{task}

Constraints:
{constraints or 'Use common-sense safety. Do not take irreversible external actions without approval.'}

Desired JSON schema:
{desired_json_schema or '{"goal":"...","steps":[{"tool":"...","args":{...},"reason":"..."}],"success_criteria":["..."]}'}

Return only valid JSON."""
    return {"prompt": prompt}


TOOL_REGISTRY: Dict[str, Tool] = {
    "calculator": Tool(
        "calculator", "Calculate a math expression safely.",
        {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]},
        skill_calculator,
    ),
    "memory_note_add": Tool(
        "memory_note_add", "Save a durable note to episodic/semantic memory.",
        {"type": "object", "properties": {"title": {"type": "string"}, "body": {"type": "string"}, "tags": {"type": "string"}}, "required": ["title", "body"]},
        skill_memory_note_add,
    ),
    "memory_search": Tool(
        "memory_search", "Search notes and recent episodes.",
        {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"]},
        skill_memory_search,
    ),
    "kg_add": Tool(
        "kg_add", "Add a knowledge graph triple: subject, predicate, object.",
        {"type": "object", "properties": {"subject": {"type": "string"}, "predicate": {"type": "string"}, "object": {"type": "string"}, "source": {"type": "string"}, "confidence": {"type": "number"}}, "required": ["subject", "predicate", "object"]},
        skill_kg_add,
    ),
    "kg_search": Tool(
        "kg_search", "Search knowledge graph triples.",
        {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}, "required": []},
        skill_kg_search,
    ),
    "kg_node_add": Tool(
        "kg_node_add", "Add a typed KG node with kind/status/evidence-ready fields.",
        {"type":"object","properties":{"kind":{"type":"string"},"title":{"type":"string"},"body":{"type":"string"},"status":{"type":"string"},"source":{"type":"string"},"confidence":{"type":"number"},"hp":{"type":"number"},"critic_score":{"type":"number"},"tags":{"type":"string"},"precondition":{"type":"object"},"meta":{"type":"object"},"persists":{"type":"boolean"}},"required":["title"]},
        skill_kg_node_add,
    ),
    "kg_edge_add": Tool(
        "kg_edge_add", "Add a weighted multi-channel edge between typed KG nodes.",
        {"type":"object","properties":{"src":{"type":"string"},"dst":{"type":"string"},"channel":{"type":"string"},"weight":{"type":"number"},"source":{"type":"string"}},"required":["src","dst"]},
        skill_kg_edge_add,
    ),
    "kg_evidence_add": Tool(
        "kg_evidence_add", "Add evidence for or against a typed KG node and update its HP/success.",
        {"type":"object","properties":{"node_id":{"type":"string"},"polarity":{"type":"string"},"evidence":{"type":"string"},"source":{"type":"string"}},"required":["node_id","evidence"]},
        skill_kg_evidence_add,
    ),
    "kg_retrieve": Tool(
        "kg_retrieve", "Retrieve typed KG nodes by query/kind/status using fovea scoring.",
        {"type":"object","properties":{"query":{"type":"string"},"kind":{"type":"string"},"status":{"type":"string"},"limit":{"type":"integer"}},"required":[]},
        skill_kg_retrieve,
    ),
    "kg_fovea": Tool(
        "kg_fovea", "Return the narrow typed-KG fovea for the current task.",
        {"type":"object","properties":{"query":{"type":"string"},"limit":{"type":"integer"}},"required":[]},
        skill_kg_fovea,
    ),
    "kg_inspect": Tool(
        "kg_inspect", "Inspect the typed KG: nodes, edges, hubs, counts, events.",
        {"type":"object","properties":{"limit":{"type":"integer"}},"required":[]},
        skill_kg_inspect,
    ),
    "kg_lifecycle_tick": Tool(
        "kg_lifecycle_tick", "Run deterministic KG lifecycle: decay/promote/retire.",
        {"type":"object","properties":{},"required":[]},
        skill_kg_lifecycle_tick,
    ),
    "todo_add": Tool(
        "todo_add", "Add a task to the todo queue.",
        {"type": "object", "properties": {"task": {"type": "string"}, "priority": {"type": "string"}, "due": {"type": "string"}}, "required": ["task"]},
        skill_todo_add,
    ),
    "todo_list": Tool(
        "todo_list", "List tasks in the todo queue.",
        {"type": "object", "properties": {"status": {"type": "string"}, "limit": {"type": "integer"}}, "required": []},
        skill_todo_list,
    ),
    "todo_update": Tool(
        "todo_update", "Update a todo by id.",
        {"type": "object", "properties": {"id": {"type": "integer"}, "status": {"type": "string"}, "task": {"type": "string"}, "priority": {"type": "string"}, "due": {"type": "string"}}, "required": ["id"]},
        skill_todo_update,
    ),
    "goal_add": Tool(
        "goal_add", "Add a user, agent, or shared goal. Owner is user|agent|shared; horizon is short|long. Use for explicit goals, not tiny one-off todos.",
        {"type": "object", "properties": {"title": {"type": "string"}, "body": {"type": "string"}, "owner": {"type": "string"}, "horizon": {"type": "string"}, "priority": {"type": "string"}, "due": {"type": "string"}, "status": {"type": "string"}, "parent_goal_id": {"type": "integer"}, "alignment": {"type": "object"}, "source": {"type": "string"}}, "required": ["title"]},
        skill_goal_add,
    ),
    "goal_list": Tool(
        "goal_list", "List user/agent/shared goals by owner, status, and horizon.",
        {"type": "object", "properties": {"owner": {"type": "string"}, "status": {"type": "string"}, "horizon": {"type": "string"}, "limit": {"type": "integer"}}, "required": []},
        skill_goal_list,
    ),
    "goal_update": Tool(
        "goal_update", "Update a goal, including status, priority, body, alignment notes, or last-reviewed marker.",
        {"type": "object", "properties": {"id": {"type": "integer"}, "owner": {"type": "string"}, "horizon": {"type": "string"}, "title": {"type": "string"}, "body": {"type": "string"}, "status": {"type": "string"}, "priority": {"type": "string"}, "due": {"type": "string"}, "parent_goal_id": {"type": "integer"}, "alignment": {"type": "object"}, "source": {"type": "string"}, "mark_reviewed": {"type": "boolean"}}, "required": ["id"]},
        skill_goal_update,
    ),
    "soul_read": Tool(
        "soul_read", "Read Soul.md.", {"type": "object", "properties": {}, "required": []}, skill_soul_read,
    ),
    "soul_append": Tool(
        "soul_append", "Append new stable operating guidance to Soul.md.",
        {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        skill_soul_append,
    ),
    "file_write": Tool(
        "file_write", "Write a UTF-8 file inside workspace/ only.",
        {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
        skill_file_write,
    ),
    "file_read": Tool(
        "file_read", "Read a UTF-8 file inside workspace/ only.",
        {"type": "object", "properties": {"path": {"type": "string"}, "max_chars": {"type": "integer"}}, "required": ["path"]},
        skill_file_read,
    ),
    "workspace_list": Tool(
        "workspace_list", "List files inside workspace/.",
        {"type": "object", "properties": {"path": {"type": "string"}}, "required": []},
        skill_workspace_list,
    ),
    "python_run": Tool(
        "python_run", "Run a Python .py file inside workspace/ and return stdout/stderr. Low-stakes lab runner; not a hardened sandbox.",
        {"type": "object", "properties": {"path": {"type": "string"}, "timeout_seconds": {"type": "integer"}}, "required": ["path"]},
        skill_python_run,
    ),
    "web_fetch_url": Tool(
        "web_fetch_url", "Fetch a webpage URL and return readable text. Use carefully; web can be stale or untrusted.",
        {"type": "object", "properties": {"url": {"type": "string"}, "max_chars": {"type": "integer"}}, "required": ["url"]},
        skill_web_fetch_url,
    ),

    "skill_find": Tool(
        "skill_find", "Find reusable local skill cards/patterns for a task or error. Use before coding and after failures.",
        {"type":"object","properties":{"query":{"type":"string"},"error":{"type":"string"},"context":{"type":"string"},"limit":{"type":"integer"}},"required":[]},
        skill_skill_find,
    ),
    "skill_get": Tool(
        "skill_get", "Get the full text of a reusable skill card by id.",
        {"type":"object","properties":{"skill_id":{"type":"string"}},"required":["skill_id"]},
        skill_skill_get,
    ),
    "skill_propose": Tool(
        "skill_propose", "Store a proposed reusable skill as a typed KG skill node for later retrieval. Pattern only; does not grant new external powers.",
        {"type":"object","properties":{"title":{"type":"string"},"body":{"type":"string"},"triggers":{"type":"string"},"tools":{"type":"string"},"source":{"type":"string"}},"required":["title","body"]},
        skill_skill_propose,
    ),
    "make_prompt_for_llm": Tool(
        "make_prompt_for_llm", "Build a prompt that asks another LLM to produce JSON commands for a task.",
        {"type": "object", "properties": {"task": {"type": "string"}, "constraints": {"type": "string"}, "desired_json_schema": {"type": "string"}}, "required": ["task"]},
        skill_make_prompt_for_llm,
    ),
}


def tool_specs_for_prompt() -> List[Dict[str, Any]]:
    return [
        {"name": t.name, "description": t.description, "parameters": t.parameters, "human_approval": t.human_approval}
        for t in TOOL_REGISTRY.values()
    ]


def run_tool(name: str, args: Dict[str, Any]) -> Any:
    if name not in TOOL_REGISTRY:
        raise ToolError(f"Unknown tool: {name}")
    tool = TOOL_REGISTRY[name]
    args = args or {}
    result = tool.func(**args)
    storage.log_skill(name, args, result)
    return result
