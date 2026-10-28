# Decision History Safety

Apply this rule whenever a task reads or changes `.knowledges/decisions/`.

Decision history is immutable and append-only:

- MUST NOT delete an existing decision file, decision block, or historical reference.
- MUST NOT edit, move, rename, truncate, overwrite, or reorder an existing decision block. The official recorder may atomically replace the period file only while prepending a new block and preserving every existing block byte-for-byte.
- MUST NOT run `rm`, `git rm`, `unlink`, destructive rewrite commands, or companion-tool mutation commands against decision history.
- MUST add a new decision block at the top of the selected period file when a decision changes.
- MUST use `supersedes` for corrections, replacements, withdrawals, or deprecations.
- MUST validate the history and inspect the diff after recording a decision.
- MUST treat validation failures as reportable diagnostics, never as permission to delete or rewrite the failed record.
- MUST keep `--ref` for existing Markdown files that resolve inside `.knowledges/decisions/ref/`; reject period files, absolute paths, traversal, URI values, and symlink escapes; external URLs belong in `--source` or in a local `ref/` document.

If a tool, plan, or requested workflow proposes deleting or rewriting a decision, stop before the destructive operation. Preserve the existing history and explain that the append-only policy requires a new decision record. The `.knowledges/decisions/` Markdown files are the canonical source of truth; external tools may only consume a read-only projection unless the user explicitly establishes a different project policy.

If validation fails because of a missing reference, an external URL passed as `--ref`, or malformed escaping such as a literal `\\n`, report the exact error and document corrected intent in a new decision using `supersedes` when the old ID is parseable. A new decision does not clear the old validation error; continue reporting it. If the ID is unparseable, cite the failing file and block instead of inventing a link. Never edit the failed block to make the validator output clean.
