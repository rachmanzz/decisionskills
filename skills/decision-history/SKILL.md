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
- A decision may supersede only a decision with an equal or earlier timestamp; never backdate a replacement to hide a later decision.
- When a decision contains an error, record a correction as a new decision instead of rewriting the old record.
- Keep detailed reference documents in `ref/` as separate historical files.

This is a safety invariant. Never delete, edit, move, rename, truncate, reorder, or overwrite an existing historical decision block under `.knowledges/decisions/`. Never use `rm`, `git rm`, `unlink`, destructive rewrites, or companion-tool mutation commands against decision history. The official recorder may atomically replace a period file only to prepend a new block while preserving every existing block byte-for-byte. If a decision is wrong, obsolete, or withdrawn, prepend a new correction or replacement decision and link the earlier ID with `supersedes`.

If another tool or task proposes deleting a decision, stop that operation, leave the history unchanged, and explain that the append-only policy requires a new decision instead. Verify the diff after recording to ensure older blocks are still present byte-for-byte.

Validation failure is diagnostic, not permission to clean up history. Preserve the recorded block and the exact validator output, and report the failure honestly. A new decision may document corrected intent and use `supersedes` when the original ID is parseable, but it does not make an invalid historical block valid: subsequent strict validation will still report the old error. If the old ID cannot be parsed, cite the failing file and block in the new decision instead of inventing a link. Do not claim that a correction cleared an integrity failure or conceal the remaining error. The `--ref` option is only for an existing Markdown file under `.knowledges/decisions/ref/`; put external URLs in `--source` (or cite them inside a local `ref/` file). A malformed escape such as a literal `\\n` is also documented by appending a new decision, never by editing the failed block.

The current user's explicit instruction has priority over historical decisions, but it never authorizes deleting or rewriting canonical history under this skill. If that instruction establishes durable project direction, record it as a new decision unless the user asks not to.

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

Resolve `SKILL_DIR` to the absolute directory containing this installed `SKILL.md` before running any helper. The project root is separate from the installed skill directory; do not assume the project contains `skills/decision-history/`. Run helpers with `--root` pointing to the consuming project.

For shell examples below, set `SKILL_DIR` to that resolved directory in the current shell (replace the placeholder with the actual installed location):

```bash
SKILL_DIR="/absolute/path/to/installed/decision-history"
```

Use `scripts/list_decisions.py` to search by text, scope, or tags before consequential work. By default it requires valid integrity hashes before showing results and hides superseded decisions; use `--include-superseded` when reconstructing history. For historical inspection only, `--include-unverified-legacy` permits older unhashed YAML records and labels them unverified; it never permits unhashed modern Markdown blocks or a mismatched hash. Treat unverified records as leads, not authenticated evidence. If integrity validation fails, report the error instead of treating search results as trusted. Use `scripts/migrate_decisions.py` to report older YAML records or create an explicitly requested review draft. Migration never rewrites source history automatically.

New decision blocks use Markdown-only metadata. The `### Decision` section is mandatory and must state the settled decision clearly and directly. A title is not a substitute for the decision statement.

References are optional. Create a file under `ref/` only when the decision needs supporting evidence or detailed context; do not create empty reference files and do not make the decision depend on them. When `record_decision.py` links a ref, it stores a `Reference SHA256` value so later edits to that historical evidence are detected.

`--ref` values must be relative `.md` paths under `ref/` and resolve to existing files there. Absolute paths, traversal, URI values, symlink escapes, period files, and other files under the decision directory are rejected. Do not pass an HTTP(S) URL to `--ref`; record that URL with `--source` or in a local reference document. If validation reports a missing or malformed reference, stop and report the error before taking any corrective action.

### Writing a reference file

Create the reference file before recording the decision. Use Markdown, keep it under `.knowledges/decisions/ref/`, and name it after the decision ID when possible:

```text
.knowledges/decisions/ref/DEC-YYYYMMDD-HHMMSS.md
```

The reference should identify the decision, source, retrieval time, and supporting context. A practical structure is:

```markdown
# Reference for DEC-20261028-193804

- Decision: `DEC-20261028-193804`
- Type: External project context
- Retrieved at: `2026-10-28T19:30:00+07:00`
- URL: https://example.com/project

## Context

Explain why this source supports the decision.

## Evidence

Summarize the relevant facts, observations, or excerpts without replacing the decision's direct statement.
```

Link the existing file from the decision with a path relative to `.knowledges/decisions/`:

```bash
python3 "$SKILL_DIR/scripts/record_decision.py" \
  --root . \
  --title "Example decision" \
  --decision "Use the selected approach." \
  --ref ref/DEC-20261028-193804.md
```

For a simple web source, use `--source https://example.com/project` instead of creating a reference file. Reference files are historical records too: do not edit or delete an existing one; create a new reference and a new decision with `supersedes` when the supporting information changes.

Read [references/decision-schema.md](references/decision-schema.md) for the complete format and examples.

### Finding GitHub reference context

When the user provides a GitHub repository and Markdown file path, use `scripts/find_github_reference.py`. It fetches the file through `raw.githubusercontent.com` and never accepts non-`.md` paths. Use one of the mutually exclusive read-only discovery modes when section IDs are not available:

```bash
python3 "$SKILL_DIR/scripts/find_github_reference.py" \
  --repo rachmanzz/gosuite \
  --path v3/worker/README.md \
  --list-headings
```

Use `--all` to print the complete Markdown content exactly once. These discovery modes never create a ref or decision. If the URL contains a GitHub fragment, use that fragment as the section ID; otherwise list headings or ask the user which section is relevant.

When section IDs are available, use `--id` to extract the requested Markdown sections without opening a browser or changing project files:

```bash
python3 "$SKILL_DIR/scripts/find_github_reference.py" \
  --root . \
  --repo rachmanzz/gosuite \
  --path v3/worker/README.md \
  --id 22-writing-modular-tasks-in-appeventsworkers
```

Repeat `--id` for multiple sections, use `--exclude` for nested sections that should not be included, and pass `--git-ref <branch|tag|commit>` when targeting a revision other than `main`. If the extracted context must become durable evidence, provide `--decision-id` (or `--output ref/custom.md`); the helper creates a new snapshot under `ref/` and prints the `--ref` path to use with `record_decision.py`:

```bash
python3 "$SKILL_DIR/scripts/find_github_reference.py" \
  --root . \
  --repo rachmanzz/gosuite \
  --path v3/worker/README.md \
  --id 22-writing-modular-tasks-in-appeventsworkers \
  --exclude benchmarks \
  --decision-id DEC-20261028-200000
```

The helper never overwrites an existing reference snapshot. A public repository needs no token; a private repository may use `GITHUB_TOKEN`. The branch or tag is mutable, so the snapshot also records the requested revision, source URL, retrieval timestamp, and extracted-content SHA256.

### Evidence discovery and persistence workflow

Use this workflow when supporting material may influence a decision. Discovery and persistence are separate actions:

1. Identify the source mode:
   - **With a GitHub repository:** provide `repo` and `path`; use `--id` when section IDs are known, or use `--list-headings` / `--all` when they are not.
   - **Without a repository:** use a user-provided URL, local file, pasted material, or conversation context. Do not invent a repository or use the GitHub helper without a real `repo` and `path`.
2. **Discover, read-only:** fetch or inspect the material and present the relevant facts, source details, and missing information. Do not create a `ref` file or decision during discovery. Do not pass `--decision-id` or `--output` to the GitHub helper in this mode.
3. **Add argumentation:** explain why the evidence is relevant, distinguish source facts from AI interpretation, identify limitations, and check for conflicts with existing decisions. This stage must not modify `.knowledges/decisions/`.
4. **Choose persistence:**
   - **Without a reference:** keep the evidence temporary and record only the settled decision, using `References: none` and `--source` when a simple source link is useful.
   - **With a reference:** only after the user requests persistence or durable evidence is clearly required, create a Markdown file under `ref/` containing the source metadata, context, evidence, interpretation, and limitations. A GitHub snapshot may be created with `--decision-id` or `--output`; other references should be written manually.
5. **Record the decision:** after the reference exists, link it with `--ref`. If no reference was created, do not create an empty placeholder; record the decision without `--ref`.
6. **Validate and report:** run the validator with `--require-integrity`; add `--require-history` when the project should already contain decisions. When the project is in a Git worktree, also pass `--against-git-ref` with a trusted revision that predates the changes being checked. Preserve any failed record and report errors honestly, including errors that remain after an append-only correction. Never delete or rewrite evidence or decision history to obtain a clean validation result.

The Git baseline check detects edits or removals from tracked decision and reference files while allowing valid new blocks at the top of tracked period files. Current files alone cannot prove that the newest block was not deleted before the trusted baseline was established.

The default behavior is discovery without persistence. A request to “find,” “search,” or “analyze” evidence does not by itself authorize creating a reference file or appending a decision. A separate explicit request such as “save this as a ref” or “record this decision” authorizes the corresponding persistence step.

## Reporting decisions

When a decision influenced the work, mention its ID in the response or implementation notes. When creating a replacement decision, state which earlier decision it supersedes. Preserve the full history so a future agent can reconstruct why the project reached its current state.

The recorder serializes helper writers for the same project on the current host, including writers targeting different period files, and uses an atomic replacement after flushing the new file. This protects cooperating recorder processes from duplicate IDs and lost updates. It does not protect against manual editors or unrelated writers that bypass the recorder.
