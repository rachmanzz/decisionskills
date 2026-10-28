# Decision History

An installable agent skill for preserving project decisions as timestamped, traceable, append-only Markdown history.

Decision History helps AI agents and teams understand why a project reached its current state and use that context before taking consequential steps.

## Install

Install the skill into a project with the `skills` CLI:

```bash
npx skills add rachmanzz/decisionskills --skill decision-history
```

For local development:

```bash
npx skills add ./decisionskills --skill decision-history -a codex -y
```

## What it does

The skill teaches an AI agent to:

- read relevant decisions before making consequential changes;
- record durable architectural, workflow, dependency, security, and product decisions;
- preserve timestamps and source context;
- detect superseding decisions and historical conflicts;
- keep older decisions unchanged for future traceability.

## Decision storage

Projects using the skill store decisions under their own project root:

```text
.knowledges/
└── decisions/
    ├── 2026-10.md
    ├── 2026-11-28.md
    └── ref/
        └── DEC-*.md
```

The default mode uses one file per month. Date-level files are available when a project needs finer separation.

New decisions are inserted at the top of the selected month or date file. Existing decision blocks are never edited, reordered, or deleted. A changed decision is represented by a new block using `supersedes`.

Detailed evidence, transcripts, diagrams, or research notes can be stored as separate files under `ref/` and linked from the decision block.

## Decision block example

````markdown
<!-- decision:start -->
## Use Markdown decision records

```yaml
id: "DEC-20261028-153000"
title: "Use Markdown decision records"
decided_at: "2026-10-28T15:30:00+07:00"
scope: "project"
tags: ["architecture", "history"]
supersedes: []
related: []
refs: []
source: ["conversation"]
integrity_sha256: "..."
```

### Decision

Store project decisions as Markdown files.

### Rationale

Markdown is readable by humans, agents, and version-control tools.

<!-- decision:end -->
````

## Local development

Create a new decision with the recorder:

```bash
python3 skills/decision-history/scripts/record_decision.py \
  --root . \
  --period month \
  --title "Use a shared cache" \
  --decision "Use Redis for the shared cache."
```

Use `--period date` to write to a `YYYY-MM-DD.md` file instead.

Validate decision files and their links:

```bash
python3 skills/decision-history/scripts/validate_decisions.py \
  --root . \
  --require-integrity
```

The validator checks:

- required metadata and ISO 8601 timestamps;
- newest-first ordering within each period file;
- duplicate IDs and broken decision links;
- `supersedes` cycles;
- reference paths;
- integrity hashes that detect edits to existing blocks.

Validate the skill package itself with the `skill-creator` validator:

```bash
python3 /path/to/skill-creator/scripts/quick_validate.py \
  skills/decision-history
```

## Repository layout

```text
skills/decision-history/
├── SKILL.md
├── agents/openai.yaml
├── references/decision-schema.md
└── scripts/
    ├── record_decision.py
    └── validate_decisions.py
```

The installed skill provides the workflow. Each consuming project owns its own `.knowledges/decisions/` history.

## License

MIT. See [LICENSE](LICENSE).
