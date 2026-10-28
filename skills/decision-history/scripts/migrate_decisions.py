#!/usr/bin/env python3
"""Report legacy decision blocks and optionally create a reviewable Markdown draft."""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

from validate_decisions import END_MARKER, YAML_FENCE, decision_files, extract_records


START_MARKER = "<!-- decision:start -->"


def inline(value: str) -> str:
    return f"`{value}`"


def list_value(values: object) -> str:
    if not isinstance(values, list) or not values:
        return "none"
    return ", ".join(inline(str(value)) for value in values)


def legacy_body(record: object) -> str:
    raw = getattr(record, "raw")
    block_format = getattr(record, "format")
    if block_format == "yaml":
        match = YAML_FENCE.search(raw)
        body = raw[match.end():] if match else ""
    else:
        delimiters = list(re.finditer(r"^---\s*$", raw, re.MULTILINE))
        body = raw[delimiters[1].end():] if len(delimiters) >= 2 else ""
    body = body.replace(END_MARKER, "").strip()
    body = re.sub(r"^#[ \t]+.*?\n", "", body, count=1)
    body = re.sub(r"^##[ \t]+(Decision|Rationale|Consequences|Agent action)\s*$", r"### \1", body, flags=re.MULTILINE)
    return body.strip()


def render_markdown(record: object) -> str:
    fields = getattr(record, "fields")
    title = str(fields.get("title", "Untitled decision"))
    lines = [
        START_MARKER,
        f"## {title}",
        "",
        f"- ID: {inline(str(fields.get('id', '')))}",
        f"- Decided at: {inline(str(fields.get('decided_at', '')))}",
        f"- Scope: {inline(str(fields.get('scope', 'project')))}",
        f"- Tags: {list_value(fields.get('tags', []))}",
        f"- Supersedes: {list_value(fields.get('supersedes', []))}",
        f"- Related: {list_value(fields.get('related', []))}",
        f"- References: {list_value(fields.get('refs', []))}",
        f"- Source: {list_value(fields.get('source', []))}",
        "- Integrity SHA256: ``",
        "",
        legacy_body(record),
        "",
        END_MARKER,
        "",
    ]
    draft = "\n".join(lines)
    digest = hashlib.sha256(draft.encode("utf-8")).hexdigest()
    return draft.replace("- Integrity SHA256: ``", f"- Integrity SHA256: `{digest}`", 1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="project root; defaults to the current directory")
    parser.add_argument("--draft", help="write a converted review draft outside the decision directory")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    decision_root = root / ".knowledges" / "decisions"
    records = []
    errors = []
    for path in decision_files(decision_root):
        found, file_errors, _ = extract_records(path)
        records.extend(record for record in found if record.format in ("yaml", "legacy"))
        errors.extend(file_errors)
    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    if errors:
        return 1

    if not records:
        print("No legacy decision records found.")
        return 0

    print(f"Found {len(records)} legacy decision record(s):")
    for record in records:
        print(f"- {record.fields.get('id', 'unknown')}: {record.path} ({record.format})")
    print("Source history was not changed.")

    if args.draft:
        output = Path(args.draft).resolve()
        try:
            output.relative_to(decision_root.resolve())
        except ValueError:
            pass
        else:
            print("error: draft must be outside .knowledges/decisions/", file=sys.stderr)
            return 1
        if output.exists():
            print(f"error: draft already exists: {output}", file=sys.stderr)
            return 1
        output.parent.mkdir(parents=True, exist_ok=True)
        content = (
            "# Decision migration draft\n\n"
            "This file is a review artifact. It is not part of decision history "
            "until manually reviewed and deliberately added.\n\n"
            + "\n".join(render_markdown(record) for record in records)
        )
        output.write_text(content, encoding="utf-8")
        print(f"Wrote review draft: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
