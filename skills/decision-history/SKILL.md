---
name: decision-history
description: Record and use project decisions as timestamped, traceable, append-only Markdown history. Use when a project decision must be captured, reviewed, superseded, or used to guide an AI's next steps.
metadata:
  short-description: Maintain append-only project decisions
---

# Decision History

Use this skill to preserve project decisions as durable context for humans and AI agents.

## Core rule

Decision history is append-only:

- Never edit an existing decision file.
- Never delete a decision file.
- When a decision changes, create a new decision and link it with `supersedes`.
- When a decision contains an error, record a correction as a new decision instead of rewriting the old record.
- Treat supporting reference files as historical snapshots. Add a new reference or decision when the supporting context changes.

The current user's explicit instruction has priority over historical decisions. If that instruction establishes durable project direction, record it as a new decision unless the user asks not to.

## Storage

Use the project root's `.knowledges/decisions/` directory. The recommended layout is one decision per file, grouped by month:

```text
.knowledges/
└── decisions/
    ├── YYYY-MM/
    │   └── DEC-YYYYMMDD-HHMMSS[-NN].md
    └── ref/
        └── DEC-*.md
```

Do not store a project's decisions inside the installed skill directory. The skill provides the workflow; each consuming project owns its own history.

## When to record a decision

Record a decision when it has durable impact on architecture, dependencies, security, data, workflow, product behavior, or project direction. Do not record trivial implementation details such as local variable names.

Only record a choice as settled when it is explicit in the user's request or has been confirmed during the conversation. If an important choice is still uncertain, ask for confirmation or label it as a proposal rather than treating it as an accepted decision.

## Before taking a consequential step

1. Identify the project root.
2. Search `.knowledges/decisions/` for decisions relevant to the task, using IDs, titles, tags, scope, and referenced files.
3. Follow `supersedes` links and treat the newest applicable decision in a lineage as the current one.
4. Check for conflicting decisions. Do not silently choose between two applicable decisions; explain the conflict and request clarification when it cannot be resolved from the current user instruction.
5. Use applicable decisions as constraints when planning and implementing the next step.

An index may be generated for convenience, but it is not authoritative. The Markdown decision files are the source of truth.

## Creating a decision

Each decision must have:

- a unique ID;
- a `decided_at` timestamp in ISO 8601 format with timezone;
- a concise title;
- the decision itself;
- rationale and consequences when they are known;
- scope, tags, and source information when useful;
- links to superseded or related decisions when applicable.

Use the provided `scripts/record_decision.py` helper when available. It creates a new file without overwriting an existing file. Run `scripts/validate_decisions.py` after adding or importing records.

Read [references/decision-schema.md](references/decision-schema.md) for the complete format and examples.

## Reporting decisions

When a decision influenced the work, mention its ID in the response or implementation notes. When creating a replacement decision, state which earlier decision it supersedes. Preserve the full history so a future agent can reconstruct why the project reached its current state.
