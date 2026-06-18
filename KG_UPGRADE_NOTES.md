# Typed KG / Fovea Upgrade

SoulAgentOS v10 adds a more mature local knowledge graph inspired by the Diplomacy KG design pattern.

## What changed

The old KG is still present as a simple subject → predicate → object table. The new KG lives beside it as a typed graph:

- typed nodes: `fact`, `belief`, `prediction`, `commitment`, `intent`, `artifact`, `skill`, `goal`, `note`, `user_preference`, `observation`, `hypothesis`
- node lifecycle: `proto`, `active`, `retired`, `archived`, `rejected`
- evidence ledgers: evidence-for and evidence-against per node
- HP / confidence / critic score fields
- weighted multi-channel edges: `supports`, `contradicts`, `evidence_for`, `evidence_against`, `used_together`, `implements`, `depends_on`, etc.
- fovea retrieval: a narrow ranked slice of the KG is put into the agent context
- inspection view: nodes, edges, hubs, counts, lifecycle events
- lifecycle tick: deterministic promote / decay / retire logic

## Why this matters

The agent should not shove all memory into every prompt. It should store broadly and display narrowly.

The important pattern is:

```text
broad typed KG → retrieval/fovea → small prompt slice → tool/action → evidence → lifecycle
```

## New tools

The model can now call:

```text
kg_node_add
kg_edge_add
kg_evidence_add
kg_retrieve
kg_fovea
kg_inspect
kg_lifecycle_tick
```

The old tools still exist:

```text
kg_add
kg_search
```

Use the old tools for quick triples. Use the new tools for durable world-model knowledge.

## Recommended use

When the agent learns something durable:

```json
{
  "action": {
    "tool": "kg_node_add",
    "args": {
      "kind": "belief",
      "title": "Program output is stronger evidence than prose",
      "body": "If the agent writes code, stdout/stderr should be checked before final claims.",
      "status": "active",
      "source": "lab_bench",
      "tags": "agent,debugging,lab"
    }
  }
}
```

When a run supports a claim:

```json
{
  "action": {
    "tool": "kg_evidence_add",
    "args": {
      "node_id": "belief:abc123",
      "polarity": "for",
      "evidence": "python_run returned ok=true for pi_primes.py",
      "source": "program_run"
    }
  }
}
```

When two things are related:

```json
{
  "action": {
    "tool": "kg_edge_add",
    "args": {
      "src": "artifact:script1",
      "dst": "belief:abc123",
      "channel": "supports",
      "weight": 0.8
    }
  }
}
```

## Design lineage

This version borrows the general substrate idea from a mature Diplomacy KG:

- broad storage but narrow display/fovea
- typed records instead of one undifferentiated memory blob
- deterministic lifecycle rather than LLM self-grading
- inspection snapshots so the user can see what the agent thinks it knows
- weighted multi-channel edges instead of a single undifferentiated link

The Diplomacy-specific code is not bundled; v9 implements a project-agnostic local KG.
