from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "agent.db"
SOUL_PATH = ROOT / "soul.md"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def db() -> Iterable[sqlite3.Connection]:
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS kv (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS kg (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object TEXT NOT NULL,
                source TEXT DEFAULT '',
                confidence REAL DEFAULT 0.7,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_kg_spo ON kg(subject, predicate, object);

            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                tags TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS todos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                priority TEXT DEFAULT 'normal',
                due TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS episodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                goal TEXT NOT NULL,
                final TEXT NOT NULL,
                transcript_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS skill_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                skill TEXT NOT NULL,
                args_json TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS program_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL,
                returncode INTEGER,
                stdout TEXT DEFAULT '',
                stderr TEXT DEFAULT '',
                ok INTEGER DEFAULT 0,
                source TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner TEXT NOT NULL DEFAULT 'user',
                horizon TEXT NOT NULL DEFAULT 'short',
                title TEXT NOT NULL,
                body TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'open',
                priority TEXT DEFAULT 'normal',
                due TEXT DEFAULT '',
                parent_goal_id INTEGER,
                alignment_json TEXT DEFAULT '{}',
                source TEXT DEFAULT 'manual',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_reviewed_at TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_goals_owner_status ON goals(owner, status);
            CREATE INDEX IF NOT EXISTS idx_goals_horizon_status ON goals(horizon, status);

            CREATE TABLE IF NOT EXISTS heartbeats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mode TEXT NOT NULL DEFAULT 'manual',
                mission TEXT NOT NULL,
                final TEXT DEFAULT '',
                run_id INTEGER,
                transcript_json TEXT DEFAULT '[]',
                created_at TEXT NOT NULL
            );
            """
        )
        _seed_default_agent_goals(conn)




def _seed_default_agent_goals(conn: sqlite3.Connection) -> None:
    """Create a few durable agent goals once, so the agent has a benign internal agenda."""
    row = conn.execute("SELECT COUNT(*) AS n FROM goals WHERE owner='agent'").fetchone()
    if row and int(row["n"] or 0) > 0:
        return
    now = utc_now()
    defaults = [
        (
            "agent",
            "long",
            "Advance user work without fuss",
            "Keep a bias toward producing files, todos, code, notes, and concrete progress. Visibility matters, but do not turn every low-stakes task into an approval ritual.",
            "open",
            "high",
            "system_seed",
        ),
        (
            "agent",
            "long",
            "Improve memory quality",
            "Keep useful memories organized as notes, KG triples, goals, and todos; avoid storing junk.",
            "open",
            "normal",
            "system_seed",
        ),
        (
            "agent",
            "short",
            "Use heartbeats to move user goals",
            "On each pulse, pick the highest-priority user goal or todo and make concrete progress before touching internal housekeeping.",
            "open",
            "normal",
            "system_seed",
        ),
    ]
    for owner, horizon, title, body, status, priority, source in defaults:
        conn.execute(
            "INSERT INTO goals(owner, horizon, title, body, status, priority, source, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (owner, horizon, title, body, status, priority, source, now, now),
        )

def read_soul() -> str:
    if not SOUL_PATH.exists():
        return "# Soul.md\n\nPurpose: Help the user."
    return SOUL_PATH.read_text(encoding="utf-8")


def write_soul(text: str) -> None:
    SOUL_PATH.write_text(text, encoding="utf-8")


def set_kv(key: str, value: Any) -> None:
    payload = json.dumps(value, ensure_ascii=False)
    with db() as conn:
        conn.execute(
            "INSERT INTO kv(key, value, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, payload, utc_now()),
        )


def get_kv(key: str, default: Any = None) -> Any:
    with db() as conn:
        row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except json.JSONDecodeError:
        return row["value"]



def get_profile() -> Dict[str, str]:
    profile = get_kv("profile", {}) or {}
    user_name = str(profile.get("user_name") or "User").strip() or "User"
    agent_name = str(profile.get("agent_name") or "SoulAgent").strip() or "SoulAgent"
    return {"user_name": user_name, "agent_name": agent_name}


def save_profile(user_name: str = "User", agent_name: str = "SoulAgent") -> Dict[str, str]:
    profile = {
        "user_name": str(user_name or "User").strip() or "User",
        "agent_name": str(agent_name or "SoulAgent").strip() or "SoulAgent",
    }
    set_kv("profile", profile)
    return profile


def add_note(title: str, body: str, tags: str = "") -> Dict[str, Any]:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO notes(title, body, tags, created_at) VALUES(?,?,?,?)",
            (title.strip() or "Untitled", body.strip(), tags.strip(), utc_now()),
        )
        return get_note(cur.lastrowid, conn)


def get_note(note_id: int, conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    close = False
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        close = True
    row = conn.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
    if close:
        conn.close()
    return dict(row) if row else {}


def list_notes(limit: int = 25) -> List[Dict[str, Any]]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM notes ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
    return [dict(r) for r in rows]


def add_kg(subject: str, predicate: str, object_: str, source: str = "agent", confidence: float = 0.7) -> Dict[str, Any]:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO kg(subject, predicate, object, source, confidence, created_at) VALUES(?,?,?,?,?,?)",
            (subject.strip(), predicate.strip(), object_.strip(), source.strip(), float(confidence), utc_now()),
        )
        row = conn.execute("SELECT * FROM kg WHERE id=?", (cur.lastrowid,)).fetchone()
    return dict(row)


def search_kg(query: str = "", limit: int = 30) -> List[Dict[str, Any]]:
    q = f"%{query.strip()}%"
    with db() as conn:
        if query.strip():
            rows = conn.execute(
                """
                SELECT * FROM kg
                WHERE subject LIKE ? OR predicate LIKE ? OR object LIKE ? OR source LIKE ?
                ORDER BY id DESC LIMIT ?
                """,
                (q, q, q, q, int(limit)),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM kg ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
    return [dict(r) for r in rows]


def add_todo(task: str, priority: str = "normal", due: str = "") -> Dict[str, Any]:
    now = utc_now()
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO todos(task, status, priority, due, created_at, updated_at) VALUES(?,?,?,?,?,?)",
            (task.strip(), "open", priority.strip() or "normal", due.strip(), now, now),
        )
        row = conn.execute("SELECT * FROM todos WHERE id=?", (cur.lastrowid,)).fetchone()
    return dict(row)


def list_todos(status: str = "", limit: int = 100) -> List[Dict[str, Any]]:
    with db() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM todos WHERE status=? ORDER BY id DESC LIMIT ?", (status, int(limit))
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM todos ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
    return [dict(r) for r in rows]


def update_todo(todo_id: int, status: Optional[str] = None, task: Optional[str] = None, priority: Optional[str] = None, due: Optional[str] = None) -> Dict[str, Any]:
    fields = []
    vals: List[Any] = []
    for name, val in [("status", status), ("task", task), ("priority", priority), ("due", due)]:
        if val is not None:
            fields.append(f"{name}=?")
            vals.append(str(val).strip())
    fields.append("updated_at=?")
    vals.append(utc_now())
    vals.append(int(todo_id))
    with db() as conn:
        conn.execute(f"UPDATE todos SET {', '.join(fields)} WHERE id=?", vals)
        row = conn.execute("SELECT * FROM todos WHERE id=?", (int(todo_id),)).fetchone()
    return dict(row) if row else {"error": "todo not found"}


def add_episode(role: str, content: str) -> Dict[str, Any]:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO episodes(role, content, created_at) VALUES(?,?,?)",
            (role, content, utc_now()),
        )
        row = conn.execute("SELECT * FROM episodes WHERE id=?", (cur.lastrowid,)).fetchone()
    return dict(row)


def recent_episodes(limit: int = 12) -> List[Dict[str, Any]]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM episodes ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
    return [dict(r) for r in reversed(rows)]


def search_notes_and_episodes(query: str, limit: int = 12) -> Dict[str, List[Dict[str, Any]]]:
    q = f"%{query.strip()}%"
    with db() as conn:
        notes = conn.execute(
            "SELECT * FROM notes WHERE title LIKE ? OR body LIKE ? OR tags LIKE ? ORDER BY id DESC LIMIT ?",
            (q, q, q, int(limit)),
        ).fetchall()
        episodes = conn.execute(
            "SELECT * FROM episodes WHERE content LIKE ? ORDER BY id DESC LIMIT ?",
            (q, int(limit)),
        ).fetchall()
    return {"notes": [dict(r) for r in notes], "episodes": [dict(r) for r in episodes]}


def log_skill(skill: str, args: Dict[str, Any], result: Any) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO skill_log(skill, args_json, result_json, created_at) VALUES(?,?,?,?)",
            (skill, json.dumps(args, ensure_ascii=False), json.dumps(result, ensure_ascii=False, default=str), utc_now()),
        )

def _clip_text(text: str, limit: int = 1200) -> str:
    text = text or ""
    limit = max(80, int(limit or 1200))
    if len(text) <= limit:
        return text
    head = text[: max(20, limit - 80)]
    return head.rstrip() + f"\n…[clipped {len(text) - len(head)} chars]"


def add_program_run(path: str, result: Dict[str, Any], source: str = "manual") -> Dict[str, Any]:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO program_runs(path, returncode, stdout, stderr, ok, source, created_at) VALUES(?,?,?,?,?,?,?)",
            (
                path,
                result.get("returncode"),
                result.get("stdout", ""),
                result.get("stderr", ""),
                1 if result.get("ok") else 0,
                source,
                utc_now(),
            ),
        )
        row = conn.execute("SELECT * FROM program_runs WHERE id=?", (cur.lastrowid,)).fetchone()
    return dict(row)


def list_program_runs(limit: int = 20) -> List[Dict[str, Any]]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM program_runs ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
    return [dict(r) for r in rows]


def compact_recent_episodes(limit: int = 8, max_chars: int = 700) -> List[Dict[str, Any]]:
    rows = recent_episodes(limit=limit)
    out = []
    for r in rows:
        d = dict(r)
        d["content"] = _clip_text(d.get("content", ""), max_chars)
        out.append(d)
    return out


def compact_search_notes_and_episodes(query: str, limit: int = 8, max_chars: int = 900) -> Dict[str, List[Dict[str, Any]]]:
    raw = search_notes_and_episodes(query, limit=limit)
    notes = []
    for n in raw.get("notes", []):
        d = dict(n)
        d["body"] = _clip_text(d.get("body", ""), max_chars)
        notes.append(d)
    episodes = []
    for e in raw.get("episodes", []):
        d = dict(e)
        d["content"] = _clip_text(d.get("content", ""), max_chars)
        episodes.append(d)
    return {"notes": notes, "episodes": episodes}


def list_heartbeats_compact(limit: int = 5) -> List[Dict[str, Any]]:
    with db() as conn:
        rows = conn.execute(
            "SELECT id, mode, mission, final, run_id, created_at FROM heartbeats ORDER BY id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["mission"] = _clip_text(d.get("mission", ""), 500)
        d["final"] = _clip_text(d.get("final", ""), 900)
        out.append(d)
    return out


def save_run(goal: str, final: str, transcript: List[Dict[str, Any]]) -> Dict[str, Any]:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO runs(goal, final, transcript_json, created_at) VALUES(?,?,?,?)",
            (goal, final, json.dumps(transcript, ensure_ascii=False, default=str), utc_now()),
        )
        row = conn.execute("SELECT * FROM runs WHERE id=?", (cur.lastrowid,)).fetchone()
    return dict(row)


def list_runs(limit: int = 10) -> List[Dict[str, Any]]:
    with db() as conn:
        rows = conn.execute("SELECT id, goal, final, created_at FROM runs ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
    return [dict(r) for r in rows]


def get_run(run_id: int) -> Dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id=?", (int(run_id),)).fetchone()
    if not row:
        return {}
    out = dict(row)
    try:
        out["transcript"] = json.loads(out.get("transcript_json") or "[]")
    except Exception:
        out["transcript"] = []
    return out


def latest_run() -> Dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return {}
    out = dict(row)
    try:
        out["transcript"] = json.loads(out.get("transcript_json") or "[]")
    except Exception:
        out["transcript"] = []
    return out


def _goal_row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    try:
        d["alignment"] = json.loads(d.get("alignment_json") or "{}")
    except Exception:
        d["alignment"] = {}
    return d


def add_goal(
    title: str,
    body: str = "",
    owner: str = "user",
    horizon: str = "short",
    priority: str = "normal",
    due: str = "",
    status: str = "open",
    parent_goal_id: Optional[int] = None,
    alignment: Optional[Dict[str, Any]] = None,
    source: str = "manual",
) -> Dict[str, Any]:
    owner = (owner or "user").strip().lower()
    if owner not in {"user", "agent", "shared"}:
        owner = "user"
    horizon = (horizon or "short").strip().lower()
    if horizon not in {"short", "long"}:
        horizon = "short"
    status = (status or "open").strip().lower()
    if status not in {"open", "active", "blocked", "done", "dropped"}:
        status = "open"
    now = utc_now()
    with db() as conn:
        cur = conn.execute(
            """
            INSERT INTO goals(owner, horizon, title, body, status, priority, due, parent_goal_id, alignment_json, source, created_at, updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                owner, horizon, str(title or "Untitled goal").strip() or "Untitled goal", str(body or "").strip(),
                status, str(priority or "normal").strip() or "normal", str(due or "").strip(),
                parent_goal_id, json.dumps(alignment or {}, ensure_ascii=False), str(source or "manual").strip(), now, now,
            ),
        )
        row = conn.execute("SELECT * FROM goals WHERE id=?", (cur.lastrowid,)).fetchone()
    return _goal_row_to_dict(row)


def list_goals(owner: str = "", status: str = "", horizon: str = "", limit: int = 100) -> List[Dict[str, Any]]:
    clauses: List[str] = []
    vals: List[Any] = []
    if owner:
        clauses.append("owner=?")
        vals.append(owner)
    if status:
        clauses.append("status=?")
        vals.append(status)
    if horizon:
        clauses.append("horizon=?")
        vals.append(horizon)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    vals.append(int(limit))
    with db() as conn:
        rows = conn.execute(
            f"SELECT * FROM goals {where} ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, id DESC LIMIT ?",
            vals,
        ).fetchall()
    return [_goal_row_to_dict(r) for r in rows]


def update_goal(
    id: int,
    owner: Optional[str] = None,
    horizon: Optional[str] = None,
    title: Optional[str] = None,
    body: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    due: Optional[str] = None,
    parent_goal_id: Optional[int] = None,
    alignment: Optional[Dict[str, Any]] = None,
    source: Optional[str] = None,
    mark_reviewed: bool = False,
) -> Dict[str, Any]:
    fields: List[str] = []
    vals: List[Any] = []
    allowed = {
        "owner": owner, "horizon": horizon, "title": title, "body": body,
        "status": status, "priority": priority, "due": due, "parent_goal_id": parent_goal_id, "source": source,
    }
    for name, val in allowed.items():
        if val is not None:
            fields.append(f"{name}=?")
            vals.append(str(val).strip() if name != "parent_goal_id" else val)
    if alignment is not None:
        fields.append("alignment_json=?")
        vals.append(json.dumps(alignment, ensure_ascii=False))
    if mark_reviewed:
        fields.append("last_reviewed_at=?")
        vals.append(utc_now())
    fields.append("updated_at=?")
    vals.append(utc_now())
    vals.append(int(id))
    with db() as conn:
        conn.execute(f"UPDATE goals SET {', '.join(fields)} WHERE id=?", vals)
        row = conn.execute("SELECT * FROM goals WHERE id=?", (int(id),)).fetchone()
    return _goal_row_to_dict(row) if row else {"error": "goal not found"}


def save_heartbeat(mode: str, mission: str, final: str = "", run_id: Optional[int] = None, transcript: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO heartbeats(mode, mission, final, run_id, transcript_json, created_at) VALUES(?,?,?,?,?,?)",
            (mode, mission, final, run_id, json.dumps(transcript or [], ensure_ascii=False, default=str), utc_now()),
        )
        row = conn.execute("SELECT * FROM heartbeats WHERE id=?", (cur.lastrowid,)).fetchone()
    return dict(row)


def list_heartbeats(limit: int = 20) -> List[Dict[str, Any]]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM heartbeats ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["transcript"] = json.loads(d.get("transcript_json") or "[]")
        except Exception:
            d["transcript"] = []
        out.append(d)
    return out


def get_heartbeat_settings() -> Dict[str, Any]:
    settings = get_kv("heartbeat_settings", {}) or {}
    return {
        "enabled": bool(settings.get("enabled", False)),
        "interval_minutes": int(settings.get("interval_minutes", 15) or 15),
        "max_steps": int(settings.get("max_steps", 6) or 6),
        "mode": str(settings.get("mode", "work") or "work"),
    }


def save_heartbeat_settings(enabled: bool = False, interval_minutes: int = 15, max_steps: int = 6, mode: str = "work") -> Dict[str, Any]:
    settings = {
        "enabled": bool(enabled),
        "interval_minutes": max(1, int(interval_minutes or 15)),
        "max_steps": max(1, min(20, int(max_steps or 6))),
        "mode": str(mode or "work"),
    }
    set_kv("heartbeat_settings", settings)
    return settings
