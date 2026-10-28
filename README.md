# Decision History

An installable agent skill for preserving project decisions as timestamped, traceable, append-only Markdown history.

Current release: `v0.1.7`

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

For Google Antigravity (`agy`):

```bash
agy plugin install https://github.com/rachmanzz/decisionskills
```

The repository includes a root `plugin.json` for AGY. The AGY plugin manifest only packages this skill; it does not install or bundle any optional companion graph tool. AGY also receives the safety rule in `rules/decision-history-safety.md`, which forbids deleting or rewriting decision history.

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

New decision blocks use Markdown-only metadata. The `### Decision` section is required and must state exactly what was decided. References are optional and should only be added when supporting evidence or detailed context is needed. `--ref` accepts existing local files relative to `.knowledges/decisions/`; external URLs belong in `--source` or a local `ref/` document.

Detailed evidence, transcripts, diagrams, or research notes can be stored as separate Markdown files under `ref/` and linked from the decision block. Create the file first, then pass its path relative to `.knowledges/decisions/` with `--ref`. A reference should identify the decision, source, retrieval time, context, and evidence. Use `--source` for a simple external URL; do not put web URLs directly in `--ref`.

Example reference file:

```markdown
# Reference for DEC-20261028-193804

- Decision: `DEC-20261028-193804`
- Type: External project context
- Retrieved at: `2026-10-28T19:30:00+07:00`
- URL: https://example.com/project

## Context

Why this source supports the decision.

## Evidence

Relevant facts and observations.
```

Reference files are historical records. Do not edit or delete an existing reference; create a new reference and decision with `supersedes` when the evidence changes.

The recorder serializes helper writers for the same history file on the current host and replaces the file atomically after flushing it. This protects concurrent recorder processes from lost updates. Manual editors and writers that do not use the recorder are outside this guarantee.

## Decision block example

````markdown
<!-- decision:start -->
## Use PostgreSQL as the primary database

- ID: `DEC-20261028-153000`
- Decided at: `2026-10-28T15:30:00+07:00`
- Scope: `database`
- Tags: `architecture`, `data`
- Supersedes: `none`
- Related: `none`
- References: `none`
- Source: `conversation`
- Integrity SHA256: `...`

### Decision

Use PostgreSQL as the primary database for this project.

### Rationale

PostgreSQL provides the required relational features and transaction guarantees.

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

Validation errors are report-only diagnostics. Do not delete or rewrite the decision that caused an error; preserve it and append a correction with `supersedes` if needed.

The validator checks:

- required metadata and ISO 8601 timestamps;
- newest-first ordering within each period file;
- duplicate IDs and broken decision links;
- `supersedes` cycles;
- reference paths;
- integrity hashes that detect edits to existing blocks.

Search decisions before taking a consequential step:

```bash
python3 skills/decision-history/scripts/list_decisions.py \
  --root . \
  --scope database \
  --query PostgreSQL
```

The search helper hides superseded decisions by default. Use `--include-superseded` for historical review or `--json` for automation.

Inspect older YAML records without changing history:

```bash
python3 skills/decision-history/scripts/migrate_decisions.py --root .
python3 skills/decision-history/scripts/migrate_decisions.py \
  --root . \
  --draft /tmp/decision-migration-review.md
```

Migration is report-only unless a review draft is explicitly requested. The draft must remain outside `.knowledges/decisions/`; existing history is never rewritten automatically.

Run the automated test suite:

```bash
python3 -m unittest discover -s tests -v
```

Run the development checks used by CI:

```bash
python3 -m pip install -r requirements-dev.txt
ruff check skills/decision-history/scripts tests
mypy skills/decision-history/scripts tests
```

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
├── references/
│   ├── companion-tools.md
│   └── decision-schema.md
└── scripts/
    ├── list_decisions.py
    ├── migrate_decisions.py
    ├── record_decision.py
    └── validate_decisions.py
```

The repository root also contains `plugin.json` for AGY plugin discovery and `rules/decision-history-safety.md` for AGY safety enforcement.

The installed skill provides the workflow. Each consuming project owns its own `.knowledges/decisions/` history.

## Optional companion tools

The core skill does not require external tools. When graph capabilities are useful, it recommends `decisiongraph/dg` first and `Semantica` second for advanced decision intelligence. `edgextract` is mentioned only for explicit free-form Markdown extraction needs.

These are independent third-party projects, not parts, forks, wrappers, or bundled dependencies of this skill. They are never installed automatically. A user may install one manually, or explicitly ask an agent to install one. The agent must identify the official source and license before an explicit installation request.

The selection or rejection of a companion tool is recorded as an append-only project decision. Once a tool is selected, the skill does not recommend another one. If companion tools are declined, it does not recommend them again unless the user explicitly asks.

See [the companion tool reference](skills/decision-history/references/companion-tools.md) for recommendation boundaries and licenses.

## License

MIT. See [LICENSE](LICENSE).

## Security

See [SECURITY.md](SECURITY.md) for vulnerability reporting instructions.
