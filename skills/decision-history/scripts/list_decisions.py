#!/usr/bin/env python3
"""Search decision history and list applicable decisions newest first."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from validate_decisions import decision_files, extract_records


def timestamp(record: object) -> datetime:
    value = getattr(record, "fields").get("decided_at", "")
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return datetime.min


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="project root; defaults to the current directory")
    parser.add_argument("--query", help="case-insensitive text search across metadata and decision content")
    parser.add_argument("--scope", help="exact scope filter")
    parser.add_argument("--tag", action="append", default=[], help="tag filter; repeat to require multiple tags")
    parser.add_argument("--include-superseded", action="store_true")
    parser.add_argument("--limit", type=int, help="maximum number of results")
    parser.add_argument("--verbose", action="store_true", help="show legacy-format warnings")
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    decision_root = root / ".knowledges" / "decisions"
    records = []
    errors = []
    warnings = []
    for path in decision_files(decision_root):
        found, file_errors, file_warnings = extract_records(path)
        records.extend(found)
        errors.extend(file_errors)
        warnings.extend(file_warnings)
    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    if errors:
        return 1
    if args.verbose:
        for warning in warnings:
            print(f"warning: {warning}", file=sys.stderr)

    superseded = set()
    for record in records:
        values = record.fields.get("supersedes", [])
        if isinstance(values, list):
            superseded.update(values)
    query = args.query.lower() if args.query else None
    selected = []
    for record in records:
        fields = record.fields
        decision_id = fields.get("id", "")
        if not args.include_superseded and decision_id in superseded:
            continue
        if args.scope and str(fields.get("scope", "")).lower() != args.scope.lower():
            continue
        tags = {str(tag).lower() for tag in fields.get("tags", [])}
        if any(tag.lower() not in tags for tag in args.tag):
            continue
        if query and query not in record.raw.lower():
            continue
        selected.append(record)

    selected.sort(key=timestamp, reverse=True)
    if args.limit is not None:
        if args.limit < 0:
            print("error: limit must not be negative", file=sys.stderr)
            return 1
        selected = selected[: args.limit]

    if args.as_json:
        output = []
        for record in selected:
            fields = dict(record.fields)
            fields.update(
                {
                    "path": str(record.path),
                    "format": record.format,
                    "status": "superseded" if fields.get("id") in superseded else "active",
                }
            )
            output.append(fields)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0

    if not selected:
        print("No matching decisions.")
        return 0
    for record in selected:
        fields = record.fields
        decision_id = fields.get("id", "unknown")
        status = "superseded" if decision_id in superseded else "active"
        print(
            f"{decision_id} [{status}] {fields.get('decided_at', 'unknown')} "
            f"| {fields.get('scope', 'unknown')} | {fields.get('title', 'untitled')} | {record.path}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
