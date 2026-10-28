#!/usr/bin/env python3
"""Validate the structure and links of append-only decision records."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ID_PATTERN = re.compile(r"^DEC-\d{8}-\d{6}(?:-\d{2})?$")
HASH_PATTERN = re.compile(r"^integrity_sha256:\s*.*$", re.MULTILINE)


def scalar(value: str) -> Any:
    value = value.strip()
    if value.startswith('"') and value.endswith('"'):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value[1:-1]
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1].replace("''", "'")
    if value.startswith("["):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def parse_frontmatter(path: Path) -> tuple[dict[str, Any], str, list[str]]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    errors: list[str] = []
    if not lines or lines[0].strip() != "---":
        return {}, text, ["missing opening frontmatter delimiter"]
    try:
        end = lines.index("---", 1)
    except ValueError:
        return {}, text, ["missing closing frontmatter delimiter"]

    fields: dict[str, Any] = {}
    current_list: str | None = None
    for line in lines[1:end]:
        if not line.strip():
            continue
        if line.lstrip().startswith("-") and current_list:
            item = line.lstrip()[1:].strip()
            fields[current_list].append(scalar(item))
            continue
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):(?:\s*(.*))?$", line)
        if not match:
            errors.append(f"invalid frontmatter line: {line}")
            current_list = None
            continue
        key, raw_value = match.groups()
        if raw_value is None or raw_value == "":
            fields[key] = []
            current_list = key
        else:
            fields[key] = scalar(raw_value)
            current_list = key if isinstance(fields[key], list) else None
    body = "\n".join(lines[end + 1:])
    if text.endswith("\n"):
        body += "\n"
    return fields, text, errors


def decision_files(decision_root: Path) -> list[Path]:
    if not decision_root.exists():
        return []
    return sorted(
        path
        for path in decision_root.rglob("*.md")
        if "ref" not in path.relative_to(decision_root).parts
    )


def integrity_material(text: str) -> str:
    return HASH_PATTERN.sub('integrity_sha256: ""', text, count=1)


def validate(root: Path, require_integrity: bool) -> tuple[list[str], list[str]]:
    decision_root = root / ".knowledges" / "decisions"
    files = decision_files(decision_root)
    errors: list[str] = []
    warnings: list[str] = []
    records: dict[str, tuple[Path, dict[str, Any]]] = {}

    for path in files:
        fields, text, parse_errors = parse_frontmatter(path)
        for problem in parse_errors:
            errors.append(f"{path}: {problem}")

        decision_id = fields.get("id")
        if not isinstance(decision_id, str) or not ID_PATTERN.fullmatch(decision_id):
            errors.append(f"{path}: invalid or missing id")
        elif decision_id in records:
            errors.append(f"{path}: duplicate id {decision_id} (also in {records[decision_id][0]})")
        else:
            records[decision_id] = (path, fields)

        for required in ("title", "decided_at"):
            if not fields.get(required):
                errors.append(f"{path}: missing required field {required}")
        timestamp = fields.get("decided_at")
        if timestamp:
            try:
                parsed = datetime.fromisoformat(str(timestamp))
                if parsed.tzinfo is None:
                    errors.append(f"{path}: decided_at must include a timezone")
            except ValueError:
                errors.append(f"{path}: decided_at is not valid ISO 8601: {timestamp}")

        if path.stem != decision_id:
            errors.append(f"{path}: filename must match its id ({decision_id}.md)")

        digest = fields.get("integrity_sha256")
        if digest:
            expected = hashlib.sha256(integrity_material(text).encode("utf-8")).hexdigest()
            if digest != expected:
                errors.append(f"{path}: integrity_sha256 does not match file contents")
        elif require_integrity:
            errors.append(f"{path}: missing integrity_sha256")
        else:
            warnings.append(f"{path}: no integrity_sha256; structural validation only")

    for decision_id, (path, fields) in records.items():
        for field in ("supersedes", "related", "refs", "source"):
            value = fields.get(field, [])
            if not isinstance(value, list):
                errors.append(f"{path}: {field} must be a list")

        for field in ("supersedes", "related"):
            values = fields.get(field, [])
            if isinstance(values, list):
                for related_id in values:
                    if related_id not in records:
                        errors.append(f"{path}: {field} references unknown decision {related_id}")
        refs = fields.get("refs", [])
        if isinstance(refs, list):
            for ref in refs:
                ref_path = decision_root / str(ref)
                if not ref_path.is_file():
                    errors.append(f"{path}: missing reference {ref}")

    graph = {
        decision_id: [item for item in fields.get("supersedes", []) if item in records]
        for decision_id, (_, fields) in records.items()
    }
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str, trail: list[str]) -> None:
        if node in visiting:
            cycle = " -> ".join(trail + [node])
            errors.append(f"supersedes cycle detected: {cycle}")
            return
        if node in visited:
            return
        visiting.add(node)
        for parent in graph[node]:
            visit(parent, trail + [node])
        visiting.remove(node)
        visited.add(node)

    for decision_id in graph:
        visit(decision_id, [])
    return errors, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="project root; defaults to the current directory")
    parser.add_argument("--require-integrity", action="store_true")
    args = parser.parse_args()
    errors, warnings = validate(Path(args.root).resolve(), args.require_integrity)
    for warning in warnings:
        print(f"warning: {warning}")
    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    if errors:
        print(f"validation failed: {len(errors)} error(s)", file=sys.stderr)
        return 1
    print(f"validation passed: {len(warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
