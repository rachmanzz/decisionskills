#!/usr/bin/env python3
"""Validate month/date decision files and their append-only decision blocks."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


ID_PATTERN = re.compile(r"^DEC-\d{8}-\d{6}(?:-\d{2})?$")
YAML_HASH_PATTERN = re.compile(r"^integrity_sha256:\s*.*$", re.MULTILINE)
MARKDOWN_HASH_PATTERN = re.compile(r"^- Integrity SHA256:\s*.*$", re.MULTILINE)
START_MARKER = "<!-- decision:start -->"
END_MARKER = "<!-- decision:end -->"
YAML_FENCE = re.compile(r"```yaml\r?\n(.*?)\r?\n```", re.DOTALL)
MARKDOWN_FIELD = re.compile(
    r"^-[ \t]+(ID|Decided at|Scope|Tags|Supersedes|Related|References|Source|Integrity SHA256):[ \t]*(.*)$",
    re.MULTILINE,
)
FIELD_NAMES = {
    "ID": "id",
    "Decided at": "decided_at",
    "Scope": "scope",
    "Tags": "tags",
    "Supersedes": "supersedes",
    "Related": "related",
    "References": "refs",
    "Source": "source",
    "Integrity SHA256": "integrity_sha256",
}


@dataclass
class Record:
    path: Path
    fields: dict[str, Any]
    raw: str
    format: str


def clean_inline(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == "`" and value[-1] == "`":
        value = value[1:-1]
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value[1:-1]
    return value


def parse_list(value: str) -> list[str]:
    value = value.strip()
    if not value or value.lower() == "none":
        return []
    return [item for item in (clean_inline(part) for part in value.split(",")) if item]


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


def parse_yaml_metadata(text: str) -> tuple[dict[str, Any], list[str]]:
    lines = text.splitlines()
    errors: list[str] = []
    if not lines or lines[0].strip() != "---":
        return {}, ["missing opening metadata delimiter"]
    try:
        end = lines.index("---", 1)
    except ValueError:
        return {}, ["missing closing metadata delimiter"]

    fields: dict[str, Any] = {}
    current_list: str | None = None
    for line in lines[1:end]:
        if not line.strip():
            continue
        if line.lstrip().startswith("-") and current_list:
            fields[current_list].append(scalar(line.lstrip()[1:].strip()))
            continue
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):(?:\s*(.*))?$", line)
        if not match:
            errors.append(f"invalid metadata line: {line}")
            current_list = None
            continue
        key, raw_value = match.groups()
        if raw_value is None or raw_value == "":
            fields[key] = []
            current_list = key
        else:
            fields[key] = scalar(raw_value)
            current_list = key if isinstance(fields[key], list) else None
    return fields, errors


def parse_markdown_metadata(block: str) -> tuple[dict[str, Any], list[str]]:
    heading = re.search(r"^##[ \t]+(.+?)\s*$", block, re.MULTILINE)
    fields: dict[str, Any] = {"title": heading.group(1).strip()} if heading else {}
    errors: list[str] = []
    for match in MARKDOWN_FIELD.finditer(block):
        label, raw_value = match.groups()
        key = FIELD_NAMES[label]
        if key in ("tags", "supersedes", "related", "refs", "source"):
            fields[key] = parse_list(raw_value)
        else:
            fields[key] = clean_inline(raw_value)
    return fields, errors


def parse_period_block(block: str) -> tuple[dict[str, Any], list[str], str]:
    match = YAML_FENCE.search(block)
    if match:
        metadata, errors = parse_yaml_metadata(f"---\n{match.group(1)}\n---\n")
        return metadata, errors, "yaml"
    metadata, errors = parse_markdown_metadata(block)
    return metadata, errors, "markdown"


def extract_records(path: Path) -> tuple[list[Record], list[str], list[str]]:
    text = path.read_text(encoding="utf-8")
    records: list[Record] = []
    errors: list[str] = []
    warnings: list[str] = []

    if START_MARKER not in text:
        metadata, parse_errors = parse_yaml_metadata(text)
        errors.extend(f"{path}: {problem}" for problem in parse_errors)
        if not parse_errors:
            records.append(Record(path, metadata, text, "legacy"))
            warnings.append(f"{path}: legacy single-decision format; future records should use period blocks")
        return records, errors, warnings

    cursor = 0
    while True:
        start = text.find(START_MARKER, cursor)
        if start < 0:
            break
        end = text.find(END_MARKER, start + len(START_MARKER))
        if end < 0:
            errors.append(f"{path}: decision block has no closing marker")
            break
        block_end = end + len(END_MARKER)
        if text.startswith("\n", block_end):
            block_end += 1
        block = text[start:block_end]
        metadata, parse_errors, block_format = parse_period_block(block)
        errors.extend(f"{path}: {problem}" for problem in parse_errors)
        if not parse_errors:
            records.append(Record(path, metadata, block, block_format))
        cursor = block_end
    if not records and not errors:
        errors.append(f"{path}: no decision blocks found")
    return records, errors, warnings


def decision_files(decision_root: Path) -> list[Path]:
    if not decision_root.exists():
        return []
    return sorted(
        path
        for path in decision_root.rglob("*.md")
        if "ref" not in path.relative_to(decision_root).parts
    )


def integrity_material(text: str) -> str:
    text = YAML_HASH_PATTERN.sub('integrity_sha256: ""', text, count=1)
    return MARKDOWN_HASH_PATTERN.sub("- Integrity SHA256: ``", text, count=1)


def decision_content(record: Record) -> str:
    match = re.search(r"^#{2,3}[ \t]+Decision\s*$", record.raw, re.MULTILINE)
    if not match:
        return ""
    content = record.raw[match.end():]
    content = re.split(r"^#{2,3}[ \t]+", content, maxsplit=1, flags=re.MULTILINE)[0]
    return content.replace(END_MARKER, "").strip()


def validate(root: Path, require_integrity: bool) -> tuple[list[str], list[str]]:
    decision_root = root / ".knowledges" / "decisions"
    errors: list[str] = []
    warnings: list[str] = []
    records: list[Record] = []
    records_by_file: dict[Path, list[Record]] = {}

    for path in decision_files(decision_root):
        found, file_errors, file_warnings = extract_records(path)
        records.extend(found)
        records_by_file[path] = found
        errors.extend(file_errors)
        warnings.extend(file_warnings)

    by_id: dict[str, Record] = {}
    for record in records:
        path = record.path
        fields = record.fields
        decision_id = fields.get("id")
        if not isinstance(decision_id, str) or not ID_PATTERN.fullmatch(decision_id):
            errors.append(f"{path}: invalid or missing id")
        elif decision_id in by_id:
            errors.append(f"{path}: duplicate id {decision_id} (also in {by_id[decision_id].path})")
        else:
            by_id[decision_id] = record

        for required in ("title", "decided_at", "scope"):
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

        if record.format != "legacy" and not decision_content(record):
            errors.append(f"{path}: decision {decision_id} has an empty or missing Decision section")

        digest = fields.get("integrity_sha256")
        if digest:
            expected = hashlib.sha256(integrity_material(record.raw).encode("utf-8")).hexdigest()
            if digest != expected:
                errors.append(f"{path}: integrity_sha256 does not match the decision block")
        elif require_integrity:
            errors.append(f"{path}: missing integrity_sha256")
        else:
            warnings.append(f"{path}: decision {decision_id} has no integrity_sha256")

        for field in ("tags", "supersedes", "related", "refs", "source"):
            value = fields.get(field, [])
            if not isinstance(value, list):
                errors.append(f"{path}: {field} must be a list")

    for path, file_records in records_by_file.items():
        timestamps: list[datetime] = []
        for record in file_records:
            try:
                timestamps.append(datetime.fromisoformat(str(record.fields["decided_at"])))
            except (KeyError, TypeError, ValueError):
                continue
        if any(newer < older for newer, older in zip(timestamps, timestamps[1:])):
            errors.append(f"{path}: decision blocks are not newest-first")

    for record in records:
        path = record.path
        fields = record.fields
        for field in ("supersedes", "related"):
            values = fields.get(field, [])
            if isinstance(values, list):
                for related_id in values:
                    if related_id not in by_id:
                        errors.append(f"{path}: {field} references unknown decision {related_id}")
        refs = fields.get("refs", [])
        if isinstance(refs, list):
            for ref in refs:
                if not (decision_root / str(ref)).is_file():
                    errors.append(f"{path}: missing reference {ref}")

    graph = {
        decision_id: [item for item in record.fields.get("supersedes", []) if item in by_id]
        for decision_id, record in by_id.items()
    }
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str, trail: list[str]) -> None:
        if node in visiting:
            errors.append(f"supersedes cycle detected: {' -> '.join(trail + [node])}")
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
