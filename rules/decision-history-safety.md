# Decision History Safety

Apply this rule whenever a task reads or changes `.knowledges/decisions/`.

Decision history is immutable and append-only:

- MUST NOT delete an existing decision file, decision block, or historical reference.
- MUST NOT edit, move, rename, truncate, overwrite, or reorder an existing decision block.
- MUST NOT run `rm`, `git rm`, `unlink`, destructive rewrite commands, or companion-tool mutation commands against decision history.
- MUST add a new decision block at the top of the selected period file when a decision changes.
- MUST use `supersedes` for corrections, replacements, withdrawals, or deprecations.
- MUST validate the history and inspect the diff after recording a decision.

If a tool, plan, or requested workflow proposes deleting or rewriting a decision, stop before the destructive operation. Preserve the existing history and explain that the append-only policy requires a new decision record. The `.knowledges/decisions/` Markdown files are the canonical source of truth; external tools may only consume a read-only projection unless the user explicitly establishes a different project policy.
