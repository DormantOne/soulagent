from __future__ import annotations

import hashlib
import json
import re
import traceback
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Tuple

import storage
import kg_core
import skill_library
from llm_clients import LLMConfig, call_llm
from tools import run_tool, tool_specs_for_prompt


@dataclass
class AgentRunConfig:
    llm: LLMConfig
    max_steps: int = 6
    temperature: float = 0.2
    user_name: str = "User"
    agent_name: str = "SoulAgent"


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)




# Generic controller limits. These are deliberately not tied to any one task.
# They keep the model from trying to stuff a whole project, dataset, or novel into
# a single tool call. Big work should be decomposed into small observable atoms.
MAX_TOOL_ARG_CHARS = 14000
MAX_FILE_WRITE_CHARS = 10000
MAX_MODEL_RAW_PREVIEW_CHARS = 2000
MAX_TRANSCRIPT_ITEMS_FOR_PROMPT = 12
MAX_TRANSCRIPT_TEXT_FIELD = 1200
MAX_STORAGE_TEXT_FIELD = 2500
MAX_STREAM_TEXT_FIELD = 3000
MAX_STREAM_EVENT_CHARS = 30000


def _is_code_or_artifact_goal(goal: str) -> bool:
    g = (goal or "").lower()
    needles = [
        "code", "python", "program", "script", "html", "flask", "javascript",
        "write", "build", "debug", "fix", "run", "classifier", "graph", "plot",
        "compare", "analyze", "analysis", "app", "tool",
    ]
    return any(n in g for n in needles)


def _has_successful_program_run(transcript: List[Dict[str, Any]]) -> bool:
    for item in transcript:
        if item.get("tool") == "python_run":
            res = item.get("result") or {}
            if isinstance(res, dict) and res.get("ok") is True:
                return True
    return False


def _has_file_write(transcript: List[Dict[str, Any]]) -> bool:
    return any(item.get("tool") == "file_write" and isinstance(item.get("result"), dict) and item["result"].get("ok") for item in transcript)


def _completion_gate_reason(goal: str, obj: Dict[str, Any], transcript: List[Dict[str, Any]]) -> Optional[str]:
    """Generic completion gate: do not accept words as proof for artifact tasks."""
    if "final" not in obj:
        return None
    if not _is_code_or_artifact_goal(goal):
        return None
    final = str(obj.get("final") or "").lower()
    # If the answer merely explains or plans, it may be okay. If it claims code ran,
    # require actual tool evidence.
    claims_artifact = any(w in final for w in ["saved", "wrote", "created", "code is", "script", "workspace", ".py"])
    claims_run = any(w in final for w in ["ran", "output", "stdout", "stderr", "successfully", "result"])
    if claims_artifact and not _has_file_write(transcript):
        return "The final answer claims an artifact was written, but no successful file_write appears in the transcript. Continue with an atomic file_write action."
    if claims_run and not _has_successful_program_run(transcript):
        return "The final answer claims execution/results, but no successful python_run appears in the transcript. Continue with python_run or fix the program."
    # For explicit build/code goals, don't finish on step 1 with no artifact unless the final is clearly a refusal or clarification.
    if not _has_file_write(transcript) and not any(w in final for w in ["cannot", "need", "please", "clarify"]):
        return "This looks like a code/artifact goal, but no workspace artifact exists yet. Start with a small file_write or todo decomposition."
    return None


def _tool_payload_size(action: Dict[str, Any]) -> int:
    try:
        return len(json.dumps(action.get("args") or {}, ensure_ascii=False, default=str))
    except Exception:
        return MAX_TOOL_ARG_CHARS + 1


def _validate_atomic_action(action: Dict[str, Any]) -> Tuple[bool, str]:
    """Reject overlarge tool actions before they hit a tool. Generic, not task-specific."""
    if not isinstance(action, dict):
        return False, "Action is not a dictionary. Return one JSON object with action.tool and action.args."
    tool = action.get("tool")
    args = action.get("args") or {}
    if not tool:
        return False, "Missing action.tool."
    size = _tool_payload_size(action)
    if size > MAX_TOOL_ARG_CHARS:
        return False, f"Tool payload is too large ({size} chars). Break the work into smaller actions or write compact code that computes/loads data at runtime."
    if tool == "file_write":
        content = str(args.get("content") or "")
        if len(content) > MAX_FILE_WRITE_CHARS:
            return False, f"file_write content is too large ({len(content)} chars). Write a smaller scaffold first, compute data at runtime, or split into multiple files."
    return True, ""


def _hash_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", errors="replace")).hexdigest()[:16]


def _compact_value(v: Any, *, max_chars: int = MAX_TRANSCRIPT_TEXT_FIELD) -> Any:
    """Recursively shrink values for prompts, streams, storage, and biopsy.

    The agent may handle code and stdout/stderr, but the browser stream should
    never be a raw archive. Large fields get preview + char count + hash.
    """
    if isinstance(v, str):
        if len(v) > max_chars:
            return {
                "preview": v[:max_chars],
                "truncated_chars": len(v) - max_chars,
                "chars": len(v),
                "sha16": _hash_text(v),
            }
        return v
    if isinstance(v, list):
        max_items = 12 if max_chars <= MAX_TRANSCRIPT_TEXT_FIELD else 24
        out = [_compact_value(x, max_chars=max_chars) for x in v[:max_items]]
        if len(v) > max_items:
            out.append({"omitted_items": len(v) - max_items})
        return out
    if isinstance(v, dict):
        out = {}
        big_keys = {"raw_response", "raw", "messages", "content", "transcript", "transcript_json", "trace_panel"}
        for k, val in v.items():
            if k in big_keys:
                if isinstance(val, str):
                    out[k] = _compact_value(val, max_chars=min(max_chars, 1200))
                else:
                    # Keep structure for transcript/messages, but compact aggressively.
                    out[k] = _compact_value(val, max_chars=min(max_chars, 900))
            elif k == "args" and isinstance(val, dict) and "content" in val:
                vv = dict(val)
                content = str(vv.get("content") or "")
                vv["content_preview"] = content[:900]
                vv["content_chars"] = len(content)
                vv["content_sha16"] = _hash_text(content)
                vv.pop("content", None)
                out[k] = _compact_value(vv, max_chars=max_chars)
            elif k in {"stdout", "stderr", "trace", "final"} and isinstance(val, str):
                out[k] = _compact_value(val, max_chars=max_chars)
            else:
                out[k] = _compact_value(val, max_chars=max_chars)
        return out
    return v


def _compact_transcript_for_prompt(transcript: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The prompt gets a compact lab notebook, not the whole autopsy."""
    items = transcript[-MAX_TRANSCRIPT_ITEMS_FOR_PROMPT:]
    return [_compact_value(x) for x in items]


def _compact_transcript_for_storage(transcript: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Store a useful biopsy trail without saving multi-megabyte raw blobs."""
    return [_compact_value(x, max_chars=MAX_STORAGE_TEXT_FIELD) for x in transcript]


def compact_payload_for_api(obj: Any) -> Any:
    """Compact arbitrary API/biopsy payloads without dropping the top-level object."""
    return _compact_value(obj, max_chars=MAX_STORAGE_TEXT_FIELD)


def compact_event_for_stream(event: Dict[str, Any]) -> Dict[str, Any]:
    """Shrink one NDJSON event before sending it to the browser.

    This is the generic stream immune system: large model outputs, tool args,
    transcripts, run rows, and stdout/stderr are represented by previews and
    hashes. Full proof is in files/program outputs, not giant browser events.
    """
    ev = _compact_value(event, max_chars=MAX_STREAM_TEXT_FIELD)
    try:
        size = len(json.dumps(ev, ensure_ascii=False, default=str))
    except Exception:
        return {"type": event.get("type", "event"), "stream_compaction_error": True}
    if size <= MAX_STREAM_EVENT_CHARS:
        return ev
    # Emergency second pass. Preserve identity/status, drop payload bulk.
    keep = {k: ev.get(k) for k in ("type", "step", "tool", "ok", "final", "error") if k in ev}
    keep["compacted"] = True
    keep["original_event_chars"] = size
    for k in ("result", "model_json", "repair", "run"):
        if k in ev:
            keep[k] = _compact_value(ev[k], max_chars=800)
    return keep


def _work_breakdown_protocol(goal: str, transcript: List[Dict[str, Any]]) -> Dict[str, Any]:
    """A deterministic tiny planner that nudges every complex task into atoms."""
    code_goal = _is_code_or_artifact_goal(goal)
    return {
        "mode": "atomic_problem_solving",
        "principle": "Do one small observable action per model step; observe result; then choose the next action.",
        "atomic_cycle": ["decompose", "write_or_patch_one_small_artifact", "run_or_inspect", "repair_if_needed", "record_claims_with_evidence", "finalize"],
        "current_evidence": {
            "has_file_write": _has_file_write(transcript),
            "has_successful_program_run": _has_successful_program_run(transcript),
            "steps_so_far": len(transcript),
        },
        "rules": [
            "Never put a large dataset, giant constant, or full generated output inside a tool JSON payload.",
            "Prefer compact programs that compute or fetch data at runtime.",
            "For code goals, a final answer is not accepted until artifacts/tool output support it.",
            "If parsing/tool payload fails, shrink the action and try again; do not switch to verbal claims.",
        ] if code_goal else [
            "Break broad work into a small next action.",
            "Use tools only when they create or verify evidence.",
        ],
    }



def _last_error_text(transcript: List[Dict[str, Any]]) -> str:
    return skill_library.last_error_from_transcript(transcript)


def _skill_suggestions(goal: str, transcript: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Deterministic skill fovea: find likely reusable patterns before/after actions."""
    err = _last_error_text(transcript)
    recent = _compact_transcript_for_prompt(transcript[-4:]) if transcript else []
    return skill_library.find_skills(
        query=goal,
        error=err,
        context=json.dumps(recent, ensure_ascii=False, default=str)[:2000],
        limit=5,
    )


def _compact_program_runs_for_prompt(limit: int = 4) -> List[Dict[str, Any]]:
    """Program runs are useful, but full stdout/stderr can bloat prompts."""
    out = []
    for r in storage.list_program_runs(limit=limit):
        out.append({
            "id": r.get("id"),
            "path": r.get("path"),
            "ok": bool(r.get("ok")),
            "returncode": r.get("returncode"),
            "created_at": r.get("created_at"),
            "stdout_tail": str(r.get("stdout") or "")[-700:],
            "stderr_tail": str(r.get("stderr") or "")[-900:],
        })
    return out


def _context_for_prompt(context: Dict[str, Any]) -> Dict[str, Any]:
    """Final prompt budgeter: keep fovea/skills, trim archival noise."""
    c = dict(context)
    c["soul_md"] = (c.get("soul_md") or "")[:2200]
    c["recent_program_runs"] = c.get("recent_program_runs", [])[:4]
    c["recent_heartbeats"] = c.get("recent_heartbeats", [])[:2]
    c["recent_episodes"] = c.get("recent_episodes", [])[:4]
    mh = c.get("memory_hits") or {}
    c["memory_hits"] = {
        "notes": (mh.get("notes") or [])[:3],
        "episodes": (mh.get("episodes") or [])[:3],
    }
    # Keep suggestions readable. Full static cards can be retrieved with skill_get.
    ss = c.get("skill_suggestions") or {}
    c["skill_suggestions"] = {
        "count": ss.get("count", 0),
        "skills": [
            {
                "id": sk.get("id"),
                "title": sk.get("title"),
                "summary": sk.get("summary"),
                "score": sk.get("score"),
                "top_steps": (sk.get("steps") or [])[:4],
                "avoid": (sk.get("avoid") or [])[:3],
                "tools": sk.get("tools") or [],
            }
            for sk in (ss.get("skills") or [])[:5]
        ],
    }
    return _compact_value(c, max_chars=900)

def _generic_retry_entry(raw: str, step: int, issue: str, instruction: str) -> Dict[str, Any]:
    return {
        "step": step,
        "parse_repair": True,
        "issue": issue,
        "instruction_to_model": instruction,
        "raw_preview": (raw or "")[:MAX_MODEL_RAW_PREVIEW_CHARS],
    }

def _extract_json(text: str) -> Dict[str, Any]:
    """
    Parse the model's next-action JSON robustly.

    Models, especially smaller/fast models, sometimes violate the instruction by
    returning JSON plus prose, a fenced block, or two JSON objects back-to-back.
    This parser accepts the first valid JSON object it can decode and ignores
    trailing text.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("Empty model response")

    candidates: List[str] = []
    for fence in re.finditer(r"```(?:json|JSON)?\s*(.*?)\s*```", text, flags=re.S):
        candidates.append(fence.group(1).strip())
    candidates.append(text)

    decoder = json.JSONDecoder()
    errors: List[str] = []

    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate:
            continue
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
            errors.append(f"Decoded JSON was {type(obj).__name__}, not object")
        except Exception as e:
            errors.append(f"whole-candidate: {type(e).__name__}: {e}")

        for match in re.finditer(r"\{", candidate):
            try:
                obj, _end = decoder.raw_decode(candidate[match.start():])
                if isinstance(obj, dict):
                    return obj
                errors.append(f"raw-decoded JSON was {type(obj).__name__}, not object")
            except json.JSONDecodeError:
                continue

    preview = text[:500].replace("\n", "\\n")
    raise ValueError(
        "Could not parse a JSON object from model response. Preview: "
        + preview
        + " Errors: "
        + " | ".join(errors[-3:])
    )





def _normalize_tool_action(item: Any) -> Optional[Dict[str, Any]]:
    """Accept several common tool-call shapes and normalize to {tool,args}."""
    if not isinstance(item, dict):
        return None
    tool = item.get("tool") or item.get("name") or item.get("function")
    args = item.get("args") if "args" in item else item.get("arguments", {})
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            args = {"value": args}
    if not isinstance(args, dict):
        args = {}
    if tool:
        return {"tool": str(tool), "args": args}
    # Some models return {"action":{"tool":"...","args":{...}}}
    action = item.get("action")
    if isinstance(action, dict):
        return _normalize_tool_action(action)
    return None


def _function_call_blocks_present(text: str) -> bool:
    """True if a model appears to be trying to emit tool calls."""
    return bool(re.search(r"<\s*function_calls\b", text or "", flags=re.I))


def _candidate_function_call_blocks(text: str) -> List[str]:
    """Return function_call block payloads.

    Handles both well-closed blocks and the common Haiku failure where the
    response is truncated after ``<function_calls>`` while emitting a huge file
    payload. We only execute a block if JSON parsing succeeds; incomplete blocks
    are surfaced as retry feedback rather than crashing the run.
    """
    text = text or ""
    blocks = re.findall(r"<function_calls>\s*(.*?)\s*</function_calls>", text, flags=re.S | re.I)
    # If there is an opening tag but no close tag, try the tail as a candidate.
    # This can recover a complete-but-unclosed block. If it is truly truncated,
    # parsing fails and the agent gets a compact retry instruction.
    opens = list(re.finditer(r"<function_calls>", text, flags=re.I))
    closes = list(re.finditer(r"</function_calls>", text, flags=re.I))
    if opens and len(closes) < len(opens):
        tail = text[opens[-1].end():].strip()
        if tail:
            blocks.append(tail)
    return blocks


def _extract_function_call_actions(text: str) -> List[Dict[str, Any]]:
    """
    Claude/other models sometimes ignore the local JSON protocol and emit XML-ish
    <function_calls>[{"tool":"..."}]</function_calls> blocks. These blocks are
    executed before any later final answer is trusted. Invalid/truncated blocks
    return no actions; the run loop converts that into retry feedback.
    """
    text = text or ""
    blocks = _candidate_function_call_blocks(text)
    actions: List[Dict[str, Any]] = []
    seen = set()
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        fence = re.search(r"```(?:json|JSON)?\s*(.*?)\s*```", block, flags=re.S)
        if fence:
            block = fence.group(1).strip()
        try:
            data = json.loads(block)
        except Exception:
            # Try extracting the first JSON array/object from the block.
            decoder = json.JSONDecoder()
            data = None
            for i, ch in enumerate(block):
                if ch in "[{":
                    try:
                        data, _ = decoder.raw_decode(block[i:])
                        break
                    except json.JSONDecodeError:
                        continue
            if data is None:
                continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            norm = _normalize_tool_action(item)
            if not norm:
                continue
            key = json.dumps(norm, sort_keys=True, ensure_ascii=False)
            if key in seen:
                continue
            seen.add(key)
            actions.append(norm)
    return actions


def _function_call_retry_entry(raw: str, step: int) -> Dict[str, Any]:
    preview = (raw or "")[:900].replace("\n", "\n")
    return {
        "step": step,
        "parse_repair": True,
        "issue": "The model emitted a <function_calls> block, but it was not valid complete JSON, usually because the tool payload was too large or truncated.",
        "instruction_to_model": (
            "Retry with ONE compact valid JSON tool action. Do not inline thousands of digits, large datasets, or long generated constants. "
            "For pi/e tasks, write compact Python that computes digits using mpmath if available, with a short fallback or clear error message. "
            "Keep file_write content small enough to fit comfortably in one JSON object."
        ),
        "raw_preview": preview,
    }


def _same_python_run(action: Optional[Dict[str, Any]], path: str) -> bool:
    if not action:
        return False
    return (
        action.get("tool") == "python_run"
        and str((action.get("args") or {}).get("path") or "") == str(path)
    )


def _tool_execution_events(step: int, tool: str, args: Dict[str, Any], transcript: List[Dict[str, Any]], *, next_action: Optional[Dict[str, Any]] = None) -> Iterator[Dict[str, Any]]:
    """Execute one skill and yield visible events, including lab-bench auto-run for .py writes."""
    yield {"type": "tool_start", "step": step, "tool": tool, "args": args}
    try:
        result = run_tool(tool, args)
        tool_entry = {"step": step, "tool": tool, "args": args, "result": result}
        transcript.append(tool_entry)
        yield {"type": "tool_result", "step": step, "tool": tool, "args": args, "result": result}

        if tool == "file_write":
            written_path = str((args or {}).get("path") or (result or {}).get("path") or "")
            if written_path.lower().endswith(".py") and not _same_python_run(next_action, written_path):
                auto_args = {"path": written_path, "timeout_seconds": 10}
                yield {"type": "program_auto_run_start", "step": step, "tool": "python_run", "args": auto_args, "reason": "Python file was written; running it immediately."}
                try:
                    run_result = run_tool("python_run", auto_args)
                    run_entry = {"step": step, "tool": "python_run", "args": auto_args, "result": run_result, "auto_after_file_write": True}
                    transcript.append(run_entry)
                    yield {"type": "program_auto_run_result", "step": step, "tool": "python_run", "args": auto_args, "result": run_result}
                except Exception as run_e:
                    run_entry = {
                        "step": step,
                        "tool": "python_run",
                        "args": auto_args,
                        "tool_error": f"{type(run_e).__name__}: {run_e}",
                        "trace": traceback.format_exc(limit=5),
                        "auto_after_file_write": True,
                    }
                    transcript.append(run_entry)
                    yield {"type": "program_auto_run_error", "step": step, "tool": "python_run", "args": auto_args, "error": run_entry["tool_error"], "trace": run_entry["trace"]}
    except Exception as e:
        tool_entry = {
            "step": step,
            "tool": tool,
            "args": args,
            "tool_error": f"{type(e).__name__}: {e}",
            "trace": traceback.format_exc(limit=5),
        }
        transcript.append(tool_entry)
        yield {"type": "tool_error", "step": step, "tool": tool, "args": args, "error": tool_entry["tool_error"], "trace": tool_entry["trace"]}


def _relevant_context(goal: str, cfg: AgentRunConfig, transcript: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    # Keep the prompt lean. Earlier versions fed full heartbeat transcripts and
    # repeated pulse prompts back into the model, which made long-horizon mode
    # bloat itself. Biopsy still contains full detail; the model gets summaries.
    transcript = transcript or []
    return {
        "profile": {"user_name": cfg.user_name, "agent_name": cfg.agent_name},
        "soul_md": storage.read_soul(),
        "open_todos": storage.list_todos("open", limit=20),
        "open_user_goals": storage.list_goals(owner="user", status="open", limit=20),
        "open_agent_goals": storage.list_goals(owner="agent", status="open", limit=10),
        "open_shared_goals": storage.list_goals(owner="shared", status="open", limit=10),
        "recent_heartbeats": storage.list_heartbeats_compact(limit=3),
        "heartbeat_settings": storage.get_heartbeat_settings(),
        "kg_hits": storage.search_kg(goal, limit=12),
        "kg_fovea": kg_core.fovea(goal, limit=8, context={"call_kind":"agent_run", "has_user_goal": bool(goal.strip())}),
        "memory_hits": storage.compact_search_notes_and_episodes(goal, limit=5),
        "recent_episodes": storage.compact_recent_episodes(limit=5),
        "recent_program_runs": _compact_program_runs_for_prompt(limit=4),
        "skill_suggestions": _skill_suggestions(goal, transcript),
        "work_breakdown_protocol": _work_breakdown_protocol(goal, transcript),
    }


def _context_summary(context: Dict[str, Any]) -> Dict[str, Any]:
    memory_hits = context.get("memory_hits") or {}
    return {
        "profile": context.get("profile", {}),
        "soul_md_chars": len(context.get("soul_md") or ""),
        "open_todos": len(context.get("open_todos") or []),
        "open_user_goals": len(context.get("open_user_goals") or []),
        "open_agent_goals": len(context.get("open_agent_goals") or []),
        "open_shared_goals": len(context.get("open_shared_goals") or []),
        "recent_heartbeats": len(context.get("recent_heartbeats") or []),
        "kg_hits": len(context.get("kg_hits") or []),
        "memory_note_hits": len(memory_hits.get("notes") or []),
        "memory_episode_hits": len(memory_hits.get("episodes") or []),
        "recent_episodes": len(context.get("recent_episodes") or []),
        "recent_program_runs": len(context.get("recent_program_runs") or []),
        "kg_fovea_selected": len((context.get("kg_fovea") or {}).get("selected") or []),
        "skill_suggestions": len((context.get("skill_suggestions") or {}).get("skills") or []),
        "last_error_present": bool((context.get("skill_suggestions") or {}).get("error_preview")),
    }


def _system_prompt(cfg: AgentRunConfig) -> str:
    agent_name = (cfg.agent_name or "SoulAgent").strip()
    user_name = (cfg.user_name or "User").strip()
    return f"""You are {agent_name}, a local tool-using agent inside a Flask app for user {user_name}.

You are a garage-lab assistant: practical, curious, a little self-directed, and biased toward making things. You do not directly access the computer except through the listed skills. You must use a strict JSON protocol.

Current identity:
- Agent name: {agent_name}
- User display name: {user_name}

Available skills:
{_json_dumps(tool_specs_for_prompt())}

Response protocol:
Prefer EXACTLY ONE valid JSON object. No markdown. No prose outside JSON.
Do not return JSONL. Do not return an array. Do not return two objects.
The first character of your reply should be {{ and the last character should be }}.
If your provider emits <function_calls>...</function_calls> blocks anyway, those calls will be executed by the app. Do not claim a tool ran unless it appears in previous agent steps as a tool_result or program_run_result.

For a tool step:
{{
  "thought": "Brief reason for this next action. Do not reveal hidden chain-of-thought; keep it short.",
  "action": {{"tool": "tool_name", "args": {{}}}}
}}

For final answer:
{{
  "final": "Human-readable result for {user_name}.",
  "memory_writes": [{{"title":"...", "body":"...", "tags":"..."}}],
  "kg_triples": [{{"subject":"...", "predicate":"...", "object":"...", "source":"agent", "confidence":0.7}}],
  "todos": [{{"task":"...", "priority":"normal", "due":""}}]
}}

Rules:
- Use tools when they materially help: skill_find/get/propose, memory_search, kg_search, kg_node_add/edge_add/evidence_add/retrieve/fovea/inspect/lifecycle_tick, goal_list/add/update, todo_list/add/update, file_read/write/list, python_run, web_fetch_url, calculator.
- Goals are strategic intentions. Todos are concrete next actions. Keep both distinct.
- Typed KG rule: use kg_node_add for durable beliefs/facts/predictions/artifacts/skills/intents, kg_edge_add for relationships, and kg_evidence_add when a result supports or refutes a claim. Treat KG fovea as the narrow attention slice, not the whole memory.
- User goals outrank agent goals. Your own agent goals are real but secondary: they should help you work better, not become bureaucratic errands.
- For low-stakes local project work, do not repeatedly ask permission. Write workspace files, update todos, and keep moving.
- If a user goal asks for code/artifacts, prefer file_write over just describing what you would do.
- Break difficult tasks into small observable atoms: plan briefly, consult relevant skill cards when available, write/patch one file or run/inspect one thing, observe stdout/stderr, then continue. Do not solve a whole project in one model reply.
- Keep tool payloads compact. Do NOT inline thousands of digits, giant constants, large datasets, or generated output into JSON. Write code that computes or loads data at runtime instead.
- If you write or modify a Python file, run it with python_run unless there is a strong reason not to. Read stdout/stderr, then use skill_suggestions or skill_find(error=stderr) to identify known fix patterns before patching.
- During GOAL PULSE / heartbeat missions, do actual progress on the highest-priority open user goal/todo. Use multiple steps up to max_steps when useful. Do not mark internal agent goals reviewed while user tasks are open.
- When there are no user goals or todos, you may pursue agent self-goals: improve memory, organize goals, sketch useful next capabilities, or clean up workspace notes.
- Never invent tool results. Never say a program ran successfully unless python_run returned ok=true in previous steps. If a claim should be durable, write it to the typed KG with source/evidence rather than only saying it.
- Do not use web_fetch_url for legal, medical, financial, or other high-stakes advice without warning that web content can be stale/unverified.
- Do not ask to run shell commands; use the python_run skill for a workspace Python file when testing is useful.
- File operations are limited to workspace/.
- You may address the user as {user_name} in the final answer when natural.
- Final answer should be concise, concrete, and say exactly what changed.
"""


def _user_prompt(goal: str, context: Dict[str, Any], transcript: List[Dict[str, Any]]) -> str:
    compact_transcript = _compact_transcript_for_prompt(transcript)
    return f"""User goal:
{goal}

Soul and memory context:
{_json_dumps(_context_for_prompt(context))}

Compact lab notebook from previous steps:
{_json_dumps(compact_transcript)}

Controller instruction:
Use the atomic problem-solving cycle. First check skill_suggestions; use skill_find when no listed skill fits or after an error. Return exactly one next action or a final answer. For code/artifact tasks, final answers must be backed by actual file_write/python_run evidence in the lab notebook. If a previous parse/tool payload failed, shrink the next action rather than explaining the failure.

Choose the next best JSON response. If enough has been done and completion gates are satisfied, return final JSON."""


def _apply_final_side_effects(obj: Dict[str, Any]) -> Dict[str, Any]:
    side: Dict[str, Any] = {"memory_writes": [], "kg_triples": [], "todos": []}
    for m in obj.get("memory_writes", []) or []:
        try:
            side["memory_writes"].append(storage.add_note(m.get("title", "Agent note"), m.get("body", ""), m.get("tags", "agent")))
        except Exception as e:
            side["memory_writes"].append({"error": str(e), "input": m})
    for t in obj.get("kg_triples", []) or []:
        try:
            side["kg_triples"].append(storage.add_kg(t.get("subject", ""), t.get("predicate", ""), t.get("object", ""), t.get("source", "agent"), t.get("confidence", 0.7)))
        except Exception as e:
            side["kg_triples"].append({"error": str(e), "input": t})
    for todo in obj.get("todos", []) or []:
        try:
            side["todos"].append(storage.add_todo(todo.get("task", ""), todo.get("priority", "normal"), todo.get("due", "")))
        except Exception as e:
            side["todos"].append({"error": str(e), "input": todo})
    return side


def run_agent_events(goal: str, cfg: AgentRunConfig) -> Iterator[Dict[str, Any]]:
    """
    Streaming version of the agent loop.

    It yields small event dictionaries that the Flask app can send to the browser
    as NDJSON. Every model request, raw response, parsed JSON object, tool call,
    tool result, error, and final save is visible. This is intentionally verbose:
    the GUI's Biopsy button uses these events to help diagnose failure modes.
    """
    storage.init_db()
    stored_goal = goal
    if goal.lstrip().startswith(("GOAL PULSE", "HEARTBEAT")):
        first = goal.splitlines()[0].strip()
        stored_goal = first + " — see heartbeat table / biopsy for full mission."
    storage.add_episode("user", stored_goal)
    transcript: List[Dict[str, Any]] = []

    yield {
        "type": "start",
        "goal": goal,
        "profile": {"user_name": cfg.user_name, "agent_name": cfg.agent_name},
        "llm": {"provider": cfg.llm.provider, "model": cfg.llm.model, "base_url": cfg.llm.base_url or ""},
        "max_steps": cfg.max_steps,
    }

    for step in range(1, max(1, cfg.max_steps) + 1):
        context = _relevant_context(goal, cfg, transcript)
        system_prompt = _system_prompt(cfg)
        user_prompt = _user_prompt(goal, context, transcript)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        yield {"type": "step_start", "step": step, "context_summary": _context_summary(context)}
        yield {
            "type": "model_request",
            "step": step,
            "messages": messages,
            "message_char_count": sum(len(m.get("content", "")) for m in messages),
        }

        try:
            raw = call_llm(messages, cfg.llm)
            yield {"type": "model_raw", "step": step, "raw": raw}

            # First priority: execute real tool-call blocks if the provider emitted them.
            # Do this before accepting a later final JSON block, otherwise the model can
            # narrate imaginary tool use and the workspace/program run history stays empty.
            function_actions = _extract_function_call_actions(raw)
            if function_actions:
                # Generic decomposition: even if the provider emits many calls, execute
                # only the first atomic action in this step. The next step observes it.
                if len(function_actions) > 1:
                    yield {"type": "decomposition", "step": step, "message": f"Model emitted {len(function_actions)} tool calls; executing only the first atomic action this step."}
                action_item = function_actions[0]
                ok_action, why = _validate_atomic_action(action_item)
                model_entry = {"step": step, "function_calls": function_actions, "executing_first_only": True, "raw_response": raw}
                transcript.append(model_entry)
                yield {"type": "model_json", "step": step, "model_json": {"function_calls": function_actions, "executing_first_only": True}}
                if not ok_action:
                    repair_entry = _generic_retry_entry(raw, step, "Rejected overlarge or invalid atomic action: " + why, "Return one smaller JSON action. Split files, compute data at runtime, or write a scaffold first.")
                    transcript.append(repair_entry)
                    yield {"type": "action_rejected", "step": step, "repair": repair_entry}
                    continue
                tool = action_item.get("tool")
                args = action_item.get("args") or {}
                yield from _tool_execution_events(step, tool, args, transcript)
                continue

            if _function_call_blocks_present(raw):
                repair_entry = _function_call_retry_entry(raw, step)
                transcript.append(repair_entry)
                yield {"type": "parse_repair", "step": step, "repair": repair_entry}
                # Do not trust any final JSON in the same response after a broken
                # function_call block. Ask the model to retry compactly.
                continue

            obj = _extract_json(raw)
            # Accept a bare {"tool":"...","args":{...}} object as shorthand.
            if "action" not in obj and "final" not in obj and obj.get("tool"):
                obj = {"action": {"tool": obj.get("tool"), "args": obj.get("args") or {}}}
            yield {"type": "model_json", "step": step, "model_json": obj}
        except Exception as e:
            # Generic parser immune response: parsing/model-format errors become
            # observations and retries, not fatal crashes. Transport/API errors still
            # stop the run because no next action can be inferred.
            text_e = f"{type(e).__name__}: {e}"
            if isinstance(e, ValueError) and ("Could not parse" in str(e) or "Empty model response" in str(e)):
                repair_entry = _generic_retry_entry(locals().get("raw", ""), step, "Model response was not parseable as an atomic action.", "Return exactly one compact JSON object: either {\"action\":{\"tool\":...,\"args\":{...}}} or {\"final\":...}. Do not include prose, markdown, multiple actions, or huge payloads.")
                repair_entry["error"] = text_e
                repair_entry["trace"] = traceback.format_exc(limit=5)
                transcript.append(repair_entry)
                yield {"type": "parse_repair", "step": step, "repair": repair_entry}
                continue
            final = (
                "The agent could not complete the model step. "
                f"Error: {type(e).__name__}: {e}"
            )
            err_entry = {
                "step": step,
                "error": final,
                "trace": traceback.format_exc(limit=8),
            }
            transcript.append(err_entry)
            storage.add_episode("assistant", final)
            run = storage.save_run(goal, final, _compact_transcript_for_storage(transcript))
            yield {"type": "error", "step": step, "final": final, "trace": err_entry["trace"], "run": run}
            return

        model_entry = {"step": step, "model_json": obj, "raw_response": raw}
        transcript.append(model_entry)

        if "final" in obj:
            gate_reason = _completion_gate_reason(goal, obj, transcript)
            if gate_reason:
                repair_entry = _generic_retry_entry(raw, step, "Premature final answer rejected by completion gate: " + gate_reason, "Continue with the next small observable action. Use file_write/python_run/inspection tools as needed. Do not claim success until tool evidence exists.")
                transcript.append(repair_entry)
                yield {"type": "completion_gate", "step": step, "repair": repair_entry}
                continue
            final = str(obj.get("final", ""))
            side = _apply_final_side_effects(obj)
            storage.add_episode("assistant", final)
            run = storage.save_run(goal, final, _compact_transcript_for_storage(transcript))
            yield {"type": "final", "step": step, "ok": True, "final": final, "side_effects": side, "run": run, "transcript": transcript}
            return

        action = obj.get("action") or {}
        tool = action.get("tool")
        args = action.get("args") or {}
        if not tool:
            final = "The model returned JSON but did not include a final answer or a tool action."
            storage.add_episode("assistant", final)
            run = storage.save_run(goal, final, _compact_transcript_for_storage(transcript))
            yield {"type": "error", "step": step, "final": final, "run": run, "transcript": transcript}
            return

        ok_action, why = _validate_atomic_action({"tool": tool, "args": args})
        if not ok_action:
            repair_entry = _generic_retry_entry(raw, step, "Rejected overlarge or invalid atomic action: " + why, "Return one smaller JSON action. Split files, compute/load data at runtime, or write a scaffold first.")
            transcript.append(repair_entry)
            yield {"type": "action_rejected", "step": step, "repair": repair_entry}
            continue

        yield from _tool_execution_events(step, tool, args, transcript)

    final = f"Stopped after {cfg.max_steps} steps. The task may need more atomic steps. Check the trace: parse repairs, completion gates, and tool outputs show the next bottleneck."
    storage.add_episode("assistant", final)
    run = storage.save_run(goal, final, _compact_transcript_for_storage(transcript))
    yield {"type": "stopped", "ok": False, "final": final, "run": run, "transcript": transcript}


def run_agent(goal: str, cfg: AgentRunConfig) -> Dict[str, Any]:
    events = list(run_agent_events(goal, cfg))
    last = events[-1] if events else {"type": "error", "final": "No events produced."}
    transcript = last.get("transcript") or []
    if not transcript:
        # Fall back to extracting whatever was saved on the final event.
        run = last.get("run") or {}
        if run.get("transcript_json"):
            try:
                transcript = json.loads(run["transcript_json"])
            except Exception:
                transcript = []
    return {
        "ok": bool(last.get("ok", last.get("type") == "final")),
        "final": last.get("final", ""),
        "side_effects": last.get("side_effects", {}),
        "transcript": transcript,
        "run": last.get("run", {}),
        "events": [compact_event_for_stream(e) for e in events],
    }


def direct_tool_call(tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        result = run_tool(tool, args)
        return {"ok": True, "result": result}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
