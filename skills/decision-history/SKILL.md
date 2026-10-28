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

- Never edit or delete an existing decision block.
- Add each new decision at the top of the existing period file.
- The period file may change only by receiving a new block before its previous first block.
- Never rewrite, reorder, or remove older decision blocks in that file.
- When a decision changes, create a new decision and link it with `supersedes`.
- When a decision contains an error, record a correction as a new decision instead of rewriting the old record.
- Keep detailed reference documents in `ref/` as separate historical files.

This is a safety invariant. Never delete, edit, move, rename, truncate, or overwrite an existing file under `.knowledges/decisions/`. Never use `rm`, `git rm`, `unlink`, destructive rewrites, or companion-tool mutation commands against decision history. If a decision is wrong, obsolete, or withdrawn, prepend a new correction or replacement decision and link the earlier ID with `supersedes`.

If another tool or task proposes deleting a decision, stop that operation, leave the history unchanged, and explain that the append-only policy requires a new decision instead. Verify the diff after recording to ensure older blocks are still present byte-for-byte.

Validation failure is diagnostic, not permission to clean up history. Preserve the recorded block and the exact validator output, report the failure honestly, and fix forward with a new decision linked by `supersedes` when a correction is needed. The `--ref` option is only for an existing local path relative to `.knowledges/decisions/`; put external URLs in `--source` (or cite them inside a local `ref/` file). A malformed escape such as a literal `\\n` is also corrected by appending a new decision, never by editing the failed block.

The current user's explicit instruction has priority over historical decisions. If that instruction establishes durable project direction, record it as a new decision unless the user asks not to.

## Storage

Use the project root's `.knowledges/decisions/` directory. The recommended layout is one period file containing multiple decision blocks, with the newest block at the top:

```text
.knowledges/
└── decisions/
    ├── YYYY-MM.md
    │   # or YYYY-MM-DD.md when date-level files are preferred
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

## Optional companion tools

The core skill works without external tools. Companion tools are independent third-party projects and are never installed automatically.

When a user needs decision graphs, backlinks, search, or visualization, recommend `decisiongraph/dg` first. Recommend `Semantica` only when the user needs advanced causal analysis, provenance, policy reasoning, or enterprise decision intelligence. Mention `edgextract` only when the user specifically needs entity or relationship extraction from free-form Markdown.

Recommend no more than one companion tool at a time. Before making a recommendation, search decision history for the `companion-tool` scope:

- If a companion tool was selected, do not recommend another one.
- If companion tools were declined, do not recommend them again unless the user explicitly asks about them.
- If no preference exists and the task clearly benefits from a companion tool, recommend `decisiongraph/dg` first.
- If the user changes their preference, record a new decision with `supersedes` instead of editing the earlier preference.

Do not install, download, vendor, or configure a companion tool without an explicit user request. Before an explicitly requested installation, identify the official source and applicable license. Keep the tool separate from this skill; do not copy its code or make it a package dependency.

The `.knowledges/decisions/` Markdown files remain the canonical source of truth. A companion tool must not rewrite, reorder, or delete historical decision records. Read [references/companion-tools.md](references/companion-tools.md) for the recommendation boundaries and license notes.

## Creating a decision

Each decision must have:

- a unique ID;
- a `decided_at` timestamp in ISO 8601 format with timezone;
- a concise title;
- a clear `scope`;
- the decision itself;
- rationale and consequences when they are known;
- scope, tags, and source information when useful;
- links to superseded or related decisions when applicable.

Use the provided `scripts/record_decision.py` helper when available. It prepends a new decision block to the selected month or date file without changing existing blocks. Run `scripts/validate_decisions.py` after adding or importing records.

Use `scripts/list_decisions.py` to search by text, scope, or tags before consequential work. It hides superseded decisions by default; use `--include-superseded` when reconstructing history. Use `scripts/migrate_decisions.py` to report older YAML records or create an explicitly requested review draft. Migration never rewrites source history automatically.

New decision blocks use Markdown-only metadata. The `### Decision` section is mandatory and must state the settled decision clearly and directly. A title is not a substitute for the decision statement.

References are optional. Create a file under `ref/` only when the decision needs supporting evidence or detailed context; do not create empty reference files and do not make the decision depend on them.

`--ref` values must resolve to files inside `.knowledges/decisions/` before validation. Do not pass an HTTP(S) URL to `--ref`; record that URL with `--source` or in a local reference document. If validation reports a missing or malformed reference, stop and report the error before taking any corrective action.

Read [references/decision-schema.md](references/decision-schema.md) for the complete format and examples.

## Reporting decisions

When a decision influenced the work, mention its ID in the response or implementation notes. When creating a replacement decision, state which earlier decision it supersedes. Preserve the full history so a future agent can reconstruct why the project reached its current state.

The recorder serializes helper writers targeting the same history file on the current host and uses an atomic replacement after flushing the new file. This protects cooperating recorder processes from lost updates. It does not protect against manual editors or unrelated writers that bypass the recorder.
