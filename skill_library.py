from __future__ import annotations

"""
skill_library.py — reusable skill/pattern discovery for SoulAgentOS.

This is NOT a new external-power system. It is a local library of small,
inspectable work patterns the agent can consult before acting and after a
failure. The goal is to avoid one-off patches by giving the controller a way to
ask: "What known technique applies here?"

Static skills are bundled in code. Learned/proposed skills can also live in the
typed KG as kind='skill' nodes and will be surfaced by find_skills().
"""

import re
from dataclasses import dataclass, asdict
from typing import Any, Dict, List

try:
    import kg_core
except Exception:  # keep import side-effect safe during early startup
    kg_core = None  # type: ignore


@dataclass(frozen=True)
class SkillCard:
    id: str
    title: str
    summary: str
    triggers: List[str]
    use_when: List[str]
    steps: List[str]
    avoid: List[str]
    tools: List[str]
    example: str = ""
    priority: int = 5

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


STATIC_SKILLS: List[SkillCard] = [
    SkillCard(
        id="python_workspace_paths",
        title="Python workspace path hygiene",
        summary=(
            "python_run executes the script with cwd=workspace/. Files saved by the script "
            "should normally use 'output.png' or 'subdir/output.png', not 'workspace/output.png'."
        ),
        triggers=["filenotfounderror", "savefig", "workspace/", "no such file", "python_run", "matplotlib"],
        use_when=[
            "A Python program fails while saving or opening a file.",
            "stderr mentions FileNotFoundError or a path beginning with workspace/.",
            "A generated script needs to save a plot or artifact into the lab workspace.",
        ],
        steps=[
            "Remember: cwd is already workspace/ during python_run.",
            "Use Path('inscribed_polygon_analysis.png') or Path('outputs/name.png'), not Path('workspace/name.png').",
            "If using a subdirectory, create it first with Path('outputs').mkdir(exist_ok=True).",
            "Run the file again after patching and inspect stdout/stderr.",
        ],
        avoid=[
            "Do not save to 'workspace/foo.png' from inside a script already running in workspace/.",
            "Do not claim the graph exists until python_run returns ok=true and workspace_list shows it.",
        ],
        tools=["file_read", "file_write", "python_run", "workspace_list"],
        example="plt.savefig('inscribed_polygon_analysis.png', dpi=150, bbox_inches='tight')",
        priority=10,
    ),
    SkillCard(
        id="matplotlib_headless_plot",
        title="Headless matplotlib plot generation",
        summary="Make matplotlib scripts work in a non-interactive Flask/lab runner and save explicit files.",
        triggers=["matplotlib", "plt", "plot", "graph", "png", "agg", "savefig", "visualization"],
        use_when=[
            "User asks to graph/plot/visualize data with Python.",
            "A script needs to generate a PNG without opening a GUI window.",
        ],
        steps=[
            "Use import matplotlib; matplotlib.use('Agg') before importing pyplot when possible.",
            "Create the figure, save with plt.savefig('name.png'), and plt.close(fig).",
            "Print the output filename and key numerical values.",
            "Run with python_run and then workspace_list to verify the PNG exists.",
        ],
        avoid=["Do not rely on plt.show().", "Do not save to a missing directory."],
        tools=["file_write", "python_run", "workspace_list"],
        example="import matplotlib; matplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n...\nplt.savefig('plot.png')\nplt.close(fig)",
        priority=9,
    ),
    SkillCard(
        id="geometry_inscribed_polygon_circle",
        title="Inscribed polygon vs circle area",
        summary="Formula pattern for area gap between a radius-r circle and an inscribed regular n-gon.",
        triggers=["inscribed", "polygon", "circle", "area", "angle", "triangle", "sides", "regular polygon"],
        use_when=[
            "User asks about an inscribed regular polygon in a circle.",
            "Need to compute how area gap scales as number of sides increases.",
        ],
        steps=[
            "For radius r, circle_area = pi*r*r.",
            "For regular n-gon inscribed in circle, polygon_area = n*r*r*sin(2*pi/n)/2.",
            "Area gap = circle_area - polygon_area.",
            "Central angle between adjacent vertices = 360/n degrees; interior angle = (n-2)*180/n degrees.",
            "Start n=3 and increase n; graph gap vs n and gap vs central angle.",
        ],
        avoid=[
            "Do not confuse central angle 360/n with interior angle (n-2)*180/n.",
            "Do not call the convergence exponential; the small-angle area error scales roughly like 1/n^2.",
        ],
        tools=["calculator", "file_write", "python_run", "kg_node_add"],
        example="gap = math.pi*r*r - (n*r*r*math.sin(2*math.pi/n))/2",
        priority=10,
    ),
    SkillCard(
        id="atomic_code_loop",
        title="Atomic code-build loop",
        summary="For code tasks: make one small artifact, run it, inspect output, patch, rerun, then summarize.",
        triggers=["code", "python", "program", "script", "debug", "fix", "run", "error", "stderr"],
        use_when=["Any coding/artifact task.", "After a program fails."],
        steps=[
            "Write or patch one file only.",
            "Run it with python_run.",
            "Use stderr/stdout as evidence; patch only the observed failure.",
            "Do not write memory/KG/final claims until the program actually runs.",
        ],
        avoid=["Do not emit several tool calls plus a final answer in one model response.", "Do not narrate success before tool evidence."],
        tools=["file_write", "file_read", "python_run", "workspace_list"],
        priority=8,
    ),
    SkillCard(
        id="large_payload_avoidance",
        title="Avoid giant JSON payloads",
        summary="Do not inline huge constants, datasets, generated output, or whole archives into tool JSON.",
        triggers=["large", "digits", "constant", "dataset", "payload", "truncated", "jsondecodeerror", "function_calls"],
        use_when=["The task requires many digits, rows, documents, or generated output.", "A model response is truncated or malformed."],
        steps=[
            "Write compact code that computes/fetches/loads data at runtime.",
            "Split large artifacts into files or generated runtime output.",
            "Keep each file_write under the controller limit.",
        ],
        avoid=["Do not paste thousands of digits into a JSON action.", "Do not put long stdout into memory notes."],
        tools=["file_write", "python_run"],
        priority=8,
    ),
    SkillCard(
        id="runtime_dependency_probe",
        title="Runtime dependency probe",
        summary="Before using optional packages, create a tiny probe or include graceful fallback.",
        triggers=["importerror", "modulenotfounderror", "numpy", "matplotlib", "mpmath", "pandas", "sklearn"],
        use_when=["A generated script depends on optional packages.", "stderr shows missing module."],
        steps=[
            "Use a tiny script or try/except imports inside the program.",
            "Prefer stdlib math/csv/json when enough.",
            "For plotting, matplotlib is common but still verify by python_run.",
        ],
        avoid=["Do not assume packages beyond requirements.txt are installed."],
        tools=["file_write", "python_run"],
        priority=6,
    ),
]


def _tokens(s: str) -> List[str]:
    return re.findall(r"[a-zA-Z_][a-zA-Z_0-9]{2,}|\d+", (s or "").lower())


def _score_card(card: SkillCard, query: str, error: str = "", context: str = "") -> float:
    blob = " ".join([query or "", error or "", context or ""]).lower()
    toks = set(_tokens(blob))
    score = float(card.priority) * 0.05
    for trig in card.triggers:
        t = trig.lower()
        if t in blob:
            score += 2.0
        elif t in toks:
            score += 1.0
    title_toks = set(_tokens(card.title + " " + card.summary))
    score += 0.15 * len(toks & title_toks)
    if error:
        err_low = (error or "").lower()
        for trig in card.triggers:
            if trig.lower() in err_low:
                score += 3.0
    return round(score, 4)


def find_skills(query: str = "", error: str = "", context: str = "", limit: int = 5, include_learned: bool = True) -> Dict[str, Any]:
    """Return relevant static skill cards plus learned KG skill nodes."""
    scored = []
    for card in STATIC_SKILLS:
        score = _score_card(card, query, error, context)
        if score > 0.25:
            d = card.to_dict()
            d["score"] = score
            d["source"] = "static_skill_library"
            scored.append(d)
    scored.sort(key=lambda x: (-x["score"], x["id"]))

    learned: List[Dict[str, Any]] = []
    if include_learned and kg_core is not None:
        try:
            hits = kg_core.retrieve(query=" ".join([query or "", error or "", context or ""]), kind="skill", limit=max(1, int(limit)))
            for h in hits:
                learned.append({
                    "id": h.get("id"),
                    "title": h.get("title"),
                    "summary": h.get("summary") or h.get("body", ""),
                    "score": h.get("score", 0),
                    "source": "typed_kg_skill",
                    "status": h.get("status"),
                    "hp": h.get("hp"),
                })
        except Exception:
            learned = []

    return {
        "query": query,
        "error_preview": (error or "")[:1000],
        "skills": (scored[: max(0, int(limit))] + learned)[: max(0, int(limit))],
        "count": min(len(scored) + len(learned), max(0, int(limit))),
    }


def get_skill(skill_id: str) -> Dict[str, Any]:
    for card in STATIC_SKILLS:
        if card.id == skill_id:
            d = card.to_dict()
            d["source"] = "static_skill_library"
            return d
    if kg_core is not None:
        try:
            node = kg_core.node_by_id(skill_id)
            if node and node.get("kind") == "skill":
                node["source"] = "typed_kg_skill"
                return node
        except Exception:
            pass
    return {}


def last_error_from_transcript(transcript: List[Dict[str, Any]] | None) -> str:
    if not transcript:
        return ""
    for item in reversed(transcript):
        res = item.get("result") if isinstance(item, dict) else None
        if isinstance(res, dict):
            if res.get("ok") is False:
                return "\n".join(str(res.get(k) or "") for k in ("error", "stderr", "stdout") if res.get(k))[:4000]
        if item.get("tool_error"):
            return str(item.get("tool_error"))[:4000]
        if item.get("parse_repair"):
            return str(item.get("issue") or item.get("error") or "")[:4000]
    return ""
