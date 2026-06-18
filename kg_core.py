from __future__ import annotations

"""
kg_core.py — typed local KG for SoulAgentOS.

This borrows the shape of the user's mature Diplomacy KG without importing the
Diplomacy game code: broad typed storage, narrow fovea, multi-channel edges,
inspection snapshots, and simple lifecycle scoring.

Design choices:
- SQLite only; no external vector DB.
- Hashed bag-of-words handles for lightweight retrieval.
- The LLM may propose nodes/edges/evidence, but deterministic code controls
  scoring, lifecycle, and what enters the prompt fovea.
"""

import hashlib
import json
import math
import re
import uuid
from typing import Any, Dict, Iterable, List, Optional

import storage

NODE_KINDS = {
    "fact", "belief", "prediction", "commitment", "intent", "artifact",
    "skill", "goal", "note", "user_preference", "observation", "hypothesis",
}
NODE_STATUSES = {"proto", "active", "retired", "archived", "rejected"}
EDGE_CHANNELS = {
    "semantic", "used_together", "evidence_for", "evidence_against",
    "supports", "contradicts", "source_of", "implements", "mentions",
    "depends_on", "blocks", "related", "created_by", "runs", "fixes",
}

_TOKEN_RE = re.compile(r"[a-zA-Z_][a-zA-Z_0-9]{2,}|\d+")


def _stable_hash(s: str) -> int:
    return int(hashlib.sha256(s.encode("utf-8")).hexdigest()[:16], 16)


def tokenize(s: str) -> List[str]:
    return _TOKEN_RE.findall((s or "").lower())


def embed(text: str, dim: int = 64) -> List[float]:
    v = [0.0] * dim
    toks = tokenize(text) or ["empty"]
    counts: Dict[str, int] = {}
    for t in toks:
        counts[t] = counts.get(t, 0) + 1
    for t, count in counts.items():
        h = _stable_hash(t)
        sign = 1 if ((h >> 8) & 1) else -1
        weight = 1.0 + min(4, count) * 0.12
        v[h % dim] += sign * weight
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def cosine(a: List[float], b: List[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


def make_handles(content: str, ctx: str = "", kind: str = "") -> List[List[float]]:
    # Three-angle handle bundle: content, context, kind+content.
    return [
        embed(content or ""),
        embed(ctx or content or ""),
        embed(f"{kind} {content} {ctx}".strip()),
    ]


def query_handles(query: str) -> List[List[float]]:
    return [embed(query or ""), embed(f"context {query}"), embed(f"intent {query}")]


def best_handle_similarity(handles: List[List[float]], qvecs: List[List[float]]) -> float:
    best = 0.0
    for h in handles or []:
        for q in qvecs or []:
            best = max(best, cosine(h, q))
    return best


def _j(obj: Any) -> str:
    return json.dumps(obj if obj is not None else {}, ensure_ascii=False, default=str)


def _loads(s: str, default: Any) -> Any:
    try:
        return json.loads(s or "")
    except Exception:
        return default


def _now() -> str:
    return storage.utc_now()


def _clean_kind(kind: str) -> str:
    k = (kind or "fact").strip().lower()
    return k if k in NODE_KINDS else "fact"


def _clean_status(status: str) -> str:
    s = (status or "proto").strip().lower()
    return s if s in NODE_STATUSES else "proto"


def _clean_channel(channel: str) -> str:
    c = (channel or "related").strip().lower()
    return c if c in EDGE_CHANNELS else "related"


def init_schema() -> None:
    storage.init_db()
    with storage.db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS kg_nodes_v2 (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'proto',
                source TEXT DEFAULT 'agent',
                confidence REAL DEFAULT 0.7,
                hp REAL DEFAULT 1.0,
                critic_score REAL DEFAULT 0.5,
                success REAL DEFAULT 0.0,
                evidence_for_json TEXT DEFAULT '[]',
                evidence_against_json TEXT DEFAULT '[]',
                tags TEXT DEFAULT '',
                precondition_json TEXT DEFAULT '{}',
                meta_json TEXT DEFAULT '{}',
                persists INTEGER DEFAULT 0,
                times_selected INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_hit_at TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_kg_nodes_v2_kind_status ON kg_nodes_v2(kind, status);
            CREATE INDEX IF NOT EXISTS idx_kg_nodes_v2_updated ON kg_nodes_v2(updated_at);

            CREATE TABLE IF NOT EXISTS kg_edges_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                src TEXT NOT NULL,
                dst TEXT NOT NULL,
                channel TEXT NOT NULL DEFAULT 'related',
                weight REAL DEFAULT 1.0,
                source TEXT DEFAULT 'agent',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(src, dst, channel)
            );
            CREATE INDEX IF NOT EXISTS idx_kg_edges_v2_src ON kg_edges_v2(src);
            CREATE INDEX IF NOT EXISTS idx_kg_edges_v2_dst ON kg_edges_v2(dst);
            CREATE INDEX IF NOT EXISTS idx_kg_edges_v2_channel ON kg_edges_v2(channel);

            CREATE TABLE IF NOT EXISTS kg_events_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                node_id TEXT DEFAULT '',
                event_type TEXT NOT NULL,
                detail_json TEXT DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_kg_events_v2_node ON kg_events_v2(node_id);
            """
        )


def _node_id(kind: str) -> str:
    return f"{_clean_kind(kind)}:{uuid.uuid4().hex[:10]}"


def row_to_node(row) -> Dict[str, Any]:
    d = dict(row)
    d["evidence_for"] = _loads(d.pop("evidence_for_json", "[]"), [])
    d["evidence_against"] = _loads(d.pop("evidence_against_json", "[]"), [])
    d["precondition"] = _loads(d.pop("precondition_json", "{}"), {})
    d["meta"] = _loads(d.pop("meta_json", "{}"), {})
    d["persists"] = bool(d.get("persists"))
    return d


def row_to_edge(row) -> Dict[str, Any]:
    return dict(row)


def log_event(node_id: str, event_type: str, detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    init_schema()
    with storage.db() as conn:
        cur = conn.execute(
            "INSERT INTO kg_events_v2(node_id, event_type, detail_json, created_at) VALUES(?,?,?,?)",
            (node_id or "", event_type, _j(detail or {}), _now()),
        )
        row = conn.execute("SELECT * FROM kg_events_v2 WHERE id=?", (cur.lastrowid,)).fetchone()
    out = dict(row)
    out["detail"] = _loads(out.pop("detail_json", "{}"), {})
    return out


def add_node(
    kind: str,
    title: str,
    body: str = "",
    status: str = "proto",
    source: str = "agent",
    confidence: float = 0.7,
    hp: float = 1.0,
    critic_score: float = 0.5,
    tags: str = "",
    precondition: Optional[Dict[str, Any]] = None,
    meta: Optional[Dict[str, Any]] = None,
    evidence_for: Optional[List[Any]] = None,
    evidence_against: Optional[List[Any]] = None,
    persists: bool = False,
) -> Dict[str, Any]:
    init_schema()
    kind = _clean_kind(kind)
    status = _clean_status(status)
    title = str(title or "Untitled node").strip() or "Untitled node"
    now = _now()
    nid = _node_id(kind)
    with storage.db() as conn:
        conn.execute(
            """
            INSERT INTO kg_nodes_v2(id, kind, title, body, status, source, confidence, hp, critic_score, success,
                                    evidence_for_json, evidence_against_json, tags, precondition_json, meta_json,
                                    persists, times_selected, created_at, updated_at, last_hit_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                nid, kind, title, str(body or ""), status, str(source or "agent"), float(confidence),
                float(hp), float(critic_score), 0.0, _j(evidence_for or []), _j(evidence_against or []),
                str(tags or ""), _j(precondition or {}), _j(meta or {}), 1 if persists else 0, 0, now, now, "",
            ),
        )
        row = conn.execute("SELECT * FROM kg_nodes_v2 WHERE id=?", (nid,)).fetchone()
    log_event(nid, "node_created", {"kind": kind, "status": status, "source": source})
    return row_to_node(row)


def node_by_id(node_id: str) -> Dict[str, Any]:
    init_schema()
    with storage.db() as conn:
        row = conn.execute("SELECT * FROM kg_nodes_v2 WHERE id=?", (node_id,)).fetchone()
    return row_to_node(row) if row else {}


def list_nodes(kind: str = "", status: str = "", limit: int = 200) -> List[Dict[str, Any]]:
    init_schema()
    where = []
    vals: List[Any] = []
    if kind:
        where.append("kind=?")
        vals.append(_clean_kind(kind))
    if status:
        where.append("status=?")
        vals.append(_clean_status(status))
    sql = "SELECT * FROM kg_nodes_v2"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY updated_at DESC, created_at DESC LIMIT ?"
    vals.append(int(limit))
    with storage.db() as conn:
        rows = conn.execute(sql, vals).fetchall()
    return [row_to_node(r) for r in rows]


def update_node(node_id: str, **fields: Any) -> Dict[str, Any]:
    init_schema()
    allowed = {
        "kind", "title", "body", "status", "source", "confidence", "hp", "critic_score",
        "success", "tags", "precondition", "meta", "persists",
    }
    sets = []
    vals: List[Any] = []
    for k, v in fields.items():
        if k not in allowed or v is None:
            continue
        if k == "kind":
            v = _clean_kind(str(v))
        elif k == "status":
            v = _clean_status(str(v))
        elif k in {"precondition", "meta"}:
            k = k + "_json"
            v = _j(v)
        elif k == "persists":
            v = 1 if v else 0
        sets.append(f"{k}=?")
        vals.append(v)
    if not sets:
        return node_by_id(node_id)
    sets.append("updated_at=?")
    vals.append(_now())
    vals.append(node_id)
    with storage.db() as conn:
        conn.execute(f"UPDATE kg_nodes_v2 SET {', '.join(sets)} WHERE id=?", vals)
        row = conn.execute("SELECT * FROM kg_nodes_v2 WHERE id=?", (node_id,)).fetchone()
    log_event(node_id, "node_updated", {"fields": sorted(fields.keys())})
    return row_to_node(row) if row else {}


def add_evidence(node_id: str, polarity: str, evidence: str, source: str = "agent") -> Dict[str, Any]:
    init_schema()
    node = node_by_id(node_id)
    if not node:
        return {"ok": False, "error": "node not found", "node_id": node_id}
    key = "evidence_for" if str(polarity).lower() in {"for", "support", "supports", "+"} else "evidence_against"
    ev = list(node.get(key) or [])
    rec = {"text": str(evidence or ""), "source": source or "agent", "created_at": _now()}
    ev.append(rec)
    hp = float(node.get("hp") or 1.0)
    success = float(node.get("success") or 0.0)
    if key == "evidence_for":
        hp = min(4.0, hp + 0.15)
        success = min(1.0, success + 0.08)
    else:
        hp = max(0.0, hp - 0.2)
        success = max(-1.0, success - 0.12)
    field = key + "_json"
    with storage.db() as conn:
        conn.execute(
            f"UPDATE kg_nodes_v2 SET {field}=?, hp=?, success=?, updated_at=? WHERE id=?",
            (_j(ev), hp, success, _now(), node_id),
        )
    log_event(node_id, "evidence_added", {"polarity": key, "evidence": rec})
    return node_by_id(node_id)


def add_edge(src: str, dst: str, channel: str = "related", weight: float = 1.0, source: str = "agent") -> Dict[str, Any]:
    init_schema()
    channel = _clean_channel(channel)
    now = _now()
    with storage.db() as conn:
        conn.execute(
            """
            INSERT INTO kg_edges_v2(src, dst, channel, weight, source, created_at, updated_at)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(src, dst, channel) DO UPDATE SET
                weight=round(kg_edges_v2.weight + excluded.weight, 4),
                source=excluded.source,
                updated_at=excluded.updated_at
            """,
            (src, dst, channel, float(weight), source or "agent", now, now),
        )
        row = conn.execute(
            "SELECT * FROM kg_edges_v2 WHERE src=? AND dst=? AND channel=?",
            (src, dst, channel),
        ).fetchone()
    log_event(src, "edge_added", {"src": src, "dst": dst, "channel": channel, "weight": weight})
    return row_to_edge(row)


def edges_for(node_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    init_schema()
    with storage.db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM kg_edges_v2
            WHERE src=? OR dst=?
            ORDER BY ABS(weight) DESC, updated_at DESC LIMIT ?
            """,
            (node_id, node_id, int(limit)),
        ).fetchall()
    return [row_to_edge(r) for r in rows]


def list_edges(limit: int = 200) -> List[Dict[str, Any]]:
    init_schema()
    with storage.db() as conn:
        rows = conn.execute(
            "SELECT * FROM kg_edges_v2 ORDER BY ABS(weight) DESC, updated_at DESC LIMIT ?", (int(limit),)
        ).fetchall()
    return [row_to_edge(r) for r in rows]


def _precondition_holds(pc: Dict[str, Any], ctx: Dict[str, Any]) -> bool:
    if not pc:
        return True
    if "all" in pc:
        return all(_one_predicate_holds(p, ctx) for p in pc.get("all") or [])
    if "any" in pc:
        return any(_one_predicate_holds(p, ctx) for p in pc.get("any") or [])
    return True


def _one_predicate_holds(p: Dict[str, Any], ctx: Dict[str, Any]) -> bool:
    feat = str(p.get("feature") or "")
    op = str(p.get("op") or "==")
    expected = p.get("value")
    got = ctx.get(feat)
    try:
        if op == "==": return got == expected
        if op == "!=": return got != expected
        if op == ">=": return float(got) >= float(expected)
        if op == "<=": return float(got) <= float(expected)
        if op == ">": return float(got) > float(expected)
        if op == "<": return float(got) < float(expected)
        if op == "contains": return str(expected).lower() in str(got).lower()
    except Exception:
        return False
    return False


def _score_node(node: Dict[str, Any], qvecs: List[List[float]], query: str, context: Dict[str, Any]) -> float:
    content = f"{node.get('title','')}\n{node.get('body','')}\n{node.get('tags','')}"
    handles = make_handles(content, json.dumps(node.get("meta") or {}, ensure_ascii=False), node.get("kind", ""))
    sim = best_handle_similarity(handles, qvecs)
    q_toks = set(tokenize(query))
    n_toks = set(tokenize(content))
    overlap = len(q_toks & n_toks) / max(1, len(q_toks))
    hp = max(0.05, min(4.0, float(node.get("hp") or 1.0)))
    critic = max(0.0, min(1.0, float(node.get("critic_score") or 0.5)))
    conf = max(0.0, min(1.0, float(node.get("confidence") or 0.7)))
    status = node.get("status") or "proto"
    status_bonus = {"active": 0.18, "proto": 0.04, "retired": -0.35, "archived": -0.5, "rejected": -1.0}.get(status, 0.0)
    selected_penalty = min(0.25, 0.04 * int(node.get("times_selected") or 0))
    pre_ok = _precondition_holds(node.get("precondition") or {}, context or {})
    pre_penalty = 0.0 if pre_ok else -0.7
    return (sim * 1.8) + (overlap * 0.8) + (hp / 4.0) + (critic * 0.25) + (conf * 0.15) + status_bonus + pre_penalty - selected_penalty


def retrieve(query: str = "", kind: str = "", status: str = "", limit: int = 8, context: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    init_schema()
    context = context or {}
    nodes = list_nodes(kind=kind, status=status, limit=1000)
    if not nodes:
        return []
    qvecs = query_handles(query or " ")
    scored = []
    for n in nodes:
        score = _score_node(n, qvecs, query or "", context)
        # If no query, prefer active/high-hp/recent nodes.
        if not (query or "").strip():
            score = float(n.get("hp") or 1.0) + (0.5 if n.get("status") == "active" else 0.0) - 0.03 * int(n.get("times_selected") or 0)
        if score > -0.3:
            d = dict(n)
            d["score"] = round(score, 4)
            d["edges"] = edges_for(n["id"], limit=10)
            scored.append(d)
    scored.sort(key=lambda x: (-x["score"], x.get("updated_at", "")), reverse=False)
    out = sorted(scored, key=lambda x: -x["score"])[: int(limit)]
    if out:
        now = _now()
        with storage.db() as conn:
            for n in out:
                conn.execute(
                    "UPDATE kg_nodes_v2 SET times_selected=times_selected+1, last_hit_at=?, hp=min(4.0, hp+0.03) WHERE id=?",
                    (now, n["id"]),
                )
    return out


def fovea(query: str, limit: int = 8, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Narrow prompt slice: the KG's current best attention surface."""
    nodes = retrieve(query=query, limit=limit, context=context or {})
    # Keep render compact; full node details are inspectable from the KG tab / biopsy.
    lines = []
    for n in nodes:
        ev_for = len(n.get("evidence_for") or [])
        ev_against = len(n.get("evidence_against") or [])
        lines.append({
            "id": n["id"],
            "kind": n.get("kind"),
            "status": n.get("status"),
            "title": n.get("title"),
            "summary": (n.get("body") or "")[:500],
            "score": n.get("score"),
            "hp": n.get("hp"),
            "evidence": {"for": ev_for, "against": ev_against},
            "edge_count": len(n.get("edges") or []),
        })
    return {"query": query, "selected": lines, "count": len(lines)}


def inspect(limit: int = 300) -> Dict[str, Any]:
    init_schema()
    nodes = list_nodes(limit=limit)
    edges = list_edges(limit=limit)
    by_kind: Dict[str, int] = {}
    by_status: Dict[str, int] = {}
    for n in nodes:
        by_kind[n.get("kind", "?")] = by_kind.get(n.get("kind", "?"), 0) + 1
        by_status[n.get("status", "?")] = by_status.get(n.get("status", "?"), 0) + 1
    degree: Dict[str, float] = {}
    for e in edges:
        w = abs(float(e.get("weight") or 0.0))
        degree[e.get("src", "")] = degree.get(e.get("src", ""), 0.0) + w
        degree[e.get("dst", "")] = degree.get(e.get("dst", ""), 0.0) + w
    hubs = sorted([{"id": k, "degree": round(v, 4)} for k, v in degree.items() if k], key=lambda x: -x["degree"])[:10]
    return {
        "counts": {"nodes": len(nodes), "edges": len(edges), "by_kind": by_kind, "by_status": by_status},
        "nodes": nodes,
        "edges": edges,
        "hubs": hubs,
        "events": list_events(limit=80),
    }


def list_events(limit: int = 80) -> List[Dict[str, Any]]:
    init_schema()
    with storage.db() as conn:
        rows = conn.execute("SELECT * FROM kg_events_v2 ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["detail"] = _loads(d.pop("detail_json", "{}"), {})
        out.append(d)
    return out


def lifecycle_tick() -> Dict[str, Any]:
    """Small deterministic lifecycle: promote earned proto nodes, retire weak/stale nodes, decay idle hp."""
    init_schema()
    nodes = list_nodes(limit=5000)
    changed = {"promoted": [], "retired": [], "decayed": 0}
    now = _now()
    with storage.db() as conn:
        for n in nodes:
            hp = float(n.get("hp") or 1.0)
            ev_for = len(n.get("evidence_for") or [])
            ev_against = len(n.get("evidence_against") or [])
            status = n.get("status") or "proto"
            persists = bool(n.get("persists"))
            # Decay weakly unless recently selected/persistent.
            decay = 0.01 if persists else 0.025
            new_hp = max(0.0, hp - decay)
            if abs(new_hp - hp) > 1e-6:
                conn.execute("UPDATE kg_nodes_v2 SET hp=?, updated_at=? WHERE id=?", (new_hp, now, n["id"]))
                changed["decayed"] += 1
            if status == "proto" and ev_for >= 1 and ev_for >= ev_against and new_hp >= 0.75:
                conn.execute("UPDATE kg_nodes_v2 SET status='active', hp=?, updated_at=? WHERE id=?", (min(4.0, new_hp + 0.25), now, n["id"]))
                changed["promoted"].append(n["id"])
            if status in {"proto", "active"} and (ev_against >= 2 and ev_against > ev_for or new_hp < 0.12):
                conn.execute("UPDATE kg_nodes_v2 SET status='retired', updated_at=? WHERE id=?", (now, n["id"]))
                changed["retired"].append(n["id"])
    for nid in changed["promoted"]:
        log_event(nid, "lifecycle_promoted", {})
    for nid in changed["retired"]:
        log_event(nid, "lifecycle_retired", {})
    return changed


def import_simple_triples(limit: int = 500) -> Dict[str, Any]:
    """Import old subject-predicate-object triples into typed fact nodes + related edges."""
    init_schema()
    triples = storage.search_kg("", limit=limit)
    made = []
    for t in triples:
        subj_title = str(t.get("subject") or "").strip()
        obj_title = str(t.get("object") or "").strip()
        pred = str(t.get("predicate") or "related").strip()
        if not subj_title or not obj_title:
            continue
        subj = add_node("fact", subj_title, body=f"Imported legacy KG subject: {subj_title}", status="active", source="legacy_kg", confidence=float(t.get("confidence") or 0.7), tags="legacy,subject")
        obj = add_node("fact", obj_title, body=f"Imported legacy KG object: {obj_title}", status="active", source="legacy_kg", confidence=float(t.get("confidence") or 0.7), tags="legacy,object")
        edge = add_edge(subj["id"], obj["id"], channel="semantic", weight=float(t.get("confidence") or 0.7), source=f"legacy:{pred}")
        made.append({"triple_id": t.get("id"), "src": subj["id"], "dst": obj["id"], "edge": edge})
    return {"imported": len(made), "items": made[:50]}


def snapshot_for_biopsy() -> Dict[str, Any]:
    return inspect(limit=250)
