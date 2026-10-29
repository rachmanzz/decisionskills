#!/usr/bin/env python3
"""Validate month/date decision files and their append-only decision blocks."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

ID_PATTERN = re.compile(r"^DEC-\d{8}-\d{6}(?:-(?:0[2-9]|[1-9]\d+))?$")
YAML_HASH_PATTERN = re.compile(r"^integrity_sha256:\s*.*$", re.MULTILINE)
MARKDOWN_HASH_PATTERN = re.compile(r"^- Integrity SHA256:\s*.*$", re.MULTILINE)
START_MARKER = "<!-- decision:start -->"
END_MARKER = "<!-- decision:end -->"
YAML_FENCE = re.compile(r"```yaml\r?\n(.*?)\r?\n```", re.DOTALL)
MARKDOWN_FIELD = re.compile(
    r"^-[ \t]+(ID|Decided at|Scope|Tags|Supersedes|Related|References|"
    r"Reference SHA256|Source|Integrity SHA256):[ \t]*(.*)$",
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
    "Reference SHA256": "reference_hashes",
    "Source": "source",
    "Integrity SHA256": "integrity_sha256",
}


@dataclass
class Record:
    path: Path
    fields: dict[str, Any]
    raw: str
    format: str


@dataclass(frozen=True)
class SectionHeading:
    start: int
    end: int
    level: int
    title: str


def strip_html_comments(line: str, inside: bool) -> tuple[str, bool]:
    visible = ""
    cursor = 0
    while cursor < len(line):
        if inside:
            end = line.find("-->", cursor)
            if end < 0:
                break
            inside = False
            cursor = end + 3
        else:
            start = line.find("<!--", cursor)
            if start < 0:
                visible += line[cursor:]
                break
            visible += line[cursor:start]
            inside = True
            cursor = start + 4
    return visible, inside


def markdown_section_headings(block: str) -> list[SectionHeading]:
    headings: list[SectionHeading] = []
    offset = 0
    fence_character = ""
    fence_length = 0
    inside_comment = False
    for line in block.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        if not fence_character:
            content, inside_comment = strip_html_comments(content, inside_comment)
        fence = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", content)
        if fence:
            marker = fence.group(1)
            if not fence_character:
                fence_character = marker[0]
                fence_length = len(marker)
            elif marker[0] == fence_character and len(marker) >= fence_length and not fence.group(2).strip():
                fence_character = ""
            offset += len(line)
            continue
        if not fence_character and not inside_comment:
            match = re.match(r"^ {0,3}(#{2,3})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$", content)
            if match:
                headings.append(SectionHeading(offset, offset + len(line), len(match.group(1)), match.group(2).strip()))
        offset += len(line)
    return headings


def resolve_local_reference(decision_root: Path, reference: str) -> Path:
    """Resolve a reference while keeping it inside the decision directory."""

    raw_reference = reference.strip()
    if not raw_reference:
        raise ValueError("reference path must not be empty")
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", raw_reference):
        raise ValueError(
            f"reference must be a local path relative to .knowledges/decisions/, not a URI: {reference}"
        )

    relative = Path(raw_reference)
    if relative.is_absolute():
        raise ValueError(
            f"reference must be a relative path inside .knowledges/decisions/, not an absolute path: {reference}"
        )

    resolved_root = decision_root.resolve()
    candidate = (resolved_root / relative).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(
            f"reference must stay inside .knowledges/decisions/, not escape through traversal or a symlink: {reference}"
        ) from exc
    return candidate


def resolve_decision_reference(decision_root: Path, reference: str) -> Path:
    """Resolve an existing reference path confined to the Markdown ref directory."""

    candidate = resolve_local_reference(decision_root, reference)
    relative = Path(reference)
    if (
        relative.is_absolute()
        or not relative.parts
        or relative.parts[0] != "ref"
        or ".." in relative.parts
        or relative.suffix.lower() != ".md"
    ):
        raise ValueError("reference must be a relative Markdown file under .knowledges/decisions/ref/")

    resolved_root = decision_root.resolve()
    reference_root = (resolved_root / "ref").resolve()
    try:
        reference_root.relative_to(resolved_root)
        candidate.relative_to(reference_root)
    except ValueError as exc:
        raise ValueError("reference must resolve inside .knowledges/decisions/ref/") from exc
    return candidate


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
    if not value or clean_inline(value).lower() == "none":
        return []
    parts: list[str] = []
    current: list[str] = []
    in_code = False
    for character in value:
        if character == "`":
            in_code = not in_code
        if character == "," and not in_code:
            parts.append("".join(current))
            current = []
        else:
            current.append(character)
    parts.append("".join(current))
    return [item for item in (clean_inline(part) for part in parts) if item]


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
    headings = markdown_section_headings(block)
    first_body_heading = next((item for item in headings if item.level == 3), None)
    metadata = block[: first_body_heading.start] if first_body_heading else block
    title_headings = [item for item in headings if item.level == 2 and item.start < len(metadata)]
    title_heading = title_headings[0] if title_headings else None
    fields: dict[str, Any] = {"title": title_heading.title} if title_heading else {}
    errors: list[str] = []
    if len(title_headings) > 1:
        errors.append("multiple title headings in decision block")
    in_fence = False
    for raw_line in metadata.splitlines():
        line = raw_line.strip()
        if re.match(r"^ {0,3}(`{3,}|~{3,})", raw_line):
            in_fence = not in_fence
            continue
        if in_fence or not line or line == START_MARKER or line == END_MARKER or line.startswith("##"):
            continue
        match = MARKDOWN_FIELD.match(raw_line)
        if not match:
            errors.append(f"invalid metadata line: {raw_line}")
            continue
        label, raw_value = match.groups()
        key = FIELD_NAMES[label]
        if key in fields:
            errors.append(f"duplicate metadata field {label}")
            continue
        if key in ("tags", "supersedes", "related", "refs", "reference_hashes", "source"):
            fields[key] = parse_list(raw_value)
        else:
            fields[key] = clean_inline(raw_value)
    return fields, errors


def parse_period_block(block: str) -> tuple[dict[str, Any], list[str], str]:
    decision_heading = next(
        (item for item in markdown_section_headings(block) if item.level == 3 and item.title == "Decision"), None
    )
    metadata_text = block[: decision_heading.start] if decision_heading else block
    match = YAML_FENCE.search(metadata_text)
    if match:
        parsed_metadata, errors = parse_yaml_metadata(f"---\n{match.group(1)}\n---\n")
        return parsed_metadata, errors, "yaml"
    parsed_metadata, errors = parse_markdown_metadata(block)
    return parsed_metadata, errors, "markdown"


def extract_records(path: Path, text: str | None = None) -> tuple[list[Record], list[str], list[str]]:
    if text is None:
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
            if text[cursor:].strip():
                errors.append(f"{path}: text outside decision blocks")
            break
        if text[cursor:start].strip():
            errors.append(f"{path}: text outside decision blocks")
        end = text.find(END_MARKER, start + len(START_MARKER))
        if end < 0:
            errors.append(f"{path}: decision block has no closing marker")
            break
        if START_MARKER in text[start + len(START_MARKER):end]:
            errors.append(f"{path}: nested decision start marker")
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
    paths = sorted(
        path
        for path in decision_root.rglob("*.md")
        if "ref" not in path.relative_to(decision_root).parts
    )
    resolved_root = decision_root.resolve()
    for path in paths:
        try:
            path.resolve().relative_to(resolved_root)
        except ValueError as exc:
            raise ValueError(f"decision file must stay inside .knowledges/decisions/: {path}") from exc
    return paths


def check_decision_root(root: Path) -> None:
    decision_root = root / ".knowledges" / "decisions"
    try:
        decision_root.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("decision directory must stay inside the project root") from exc


def integrity_material(text: str, block_format: str | None = None) -> str:
    if block_format == "markdown":
        return MARKDOWN_HASH_PATTERN.sub("- Integrity SHA256: ``", text, count=1)
    if block_format in {"yaml", "legacy"}:
        return YAML_HASH_PATTERN.sub('integrity_sha256: ""', text, count=1)
    text = YAML_HASH_PATTERN.sub('integrity_sha256: ""', text, count=1)
    return MARKDOWN_HASH_PATTERN.sub("- Integrity SHA256: ``", text, count=1)


def decision_content(record: Record) -> str:
    headings = markdown_section_headings(record.raw)
    matches = [item for item in headings if item.level == 3 and item.title == "Decision"]
    if len(matches) != 1:
        return ""
    heading = matches[0]
    end = next((item.start for item in headings if item.start > heading.start and item.level <= 3), len(record.raw))
    content = record.raw[heading.end:end]
    return content.replace(END_MARKER, "").strip()


def aware_timestamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def scan_history(
    root: Path,
    require_integrity: bool,
    require_history: bool = False,
    allow_unhashed_legacy: bool = False,
) -> tuple[list[str], list[str], list[Record]]:
    decision_root = root / ".knowledges" / "decisions"
    errors: list[str] = []
    warnings: list[str] = []
    records: list[Record] = []
    records_by_file: dict[Path, list[Record]] = {}

    try:
        check_decision_root(root)
        paths = decision_files(decision_root)
    except ValueError as exc:
        errors.append(str(exc))
        return errors, warnings, records
    if require_history and not paths:
        errors.append(f"{decision_root}: no decision history files found")

    for path in paths:
        try:
            found, file_errors, file_warnings = extract_records(path)
        except (OSError, UnicodeError) as exc:
            errors.append(f"{path}: could not read decision file: {exc}")
            continue
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
                if parsed.tzinfo is None or parsed.utcoffset() is None:
                    errors.append(f"{path}: decided_at must include a timezone")
            except ValueError:
                errors.append(f"{path}: decided_at is not valid ISO 8601: {timestamp}")

        if record.format == "markdown":
            parsed_at = aware_timestamp(timestamp)
            if parsed_at is not None:
                expected_id = f"DEC-{parsed_at:%Y%m%d-%H%M%S}"
                if isinstance(decision_id, str) and ID_PATTERN.fullmatch(decision_id):
                    if decision_id != expected_id and not decision_id.startswith(expected_id + "-"):
                        errors.append(f"{path}: decision ID does not match decided_at")
                relative = path.relative_to(decision_root)
                expected_month = parsed_at.strftime("%Y-%m.md")
                expected_date = parsed_at.strftime("%Y-%m-%d.md")
                if len(relative.parts) != 1 or relative.name not in {expected_month, expected_date}:
                    errors.append(f"{path}: period filename does not match decided_at")

        if record.format != "legacy" and not decision_content(record):
            errors.append(f"{path}: decision {decision_id} has an empty or missing Decision section")

        digest = fields.get("integrity_sha256")
        if digest:
            expected = hashlib.sha256(integrity_material(record.raw, record.format).encode("utf-8")).hexdigest()
            if digest != expected:
                errors.append(f"{path}: integrity_sha256 does not match the decision block")
        elif require_integrity and not (allow_unhashed_legacy and record.format in {"legacy", "yaml"}):
            errors.append(f"{path}: missing integrity_sha256")
        else:
            warnings.append(f"{path}: decision {decision_id} has no integrity_sha256")

        for field in ("tags", "supersedes", "related", "refs", "reference_hashes", "source"):
            value = fields.get(field, [])
            if not isinstance(value, list):
                errors.append(f"{path}: {field} must be a list")
            else:
                for index, item in enumerate(value):
                    if not isinstance(item, str) or not item.strip():
                        errors.append(f"{path}: {field}[{index}] must be a non-empty string")

    for path, file_records in records_by_file.items():
        timestamps: list[datetime] = []
        for record in file_records:
            timestamp = aware_timestamp(record.fields.get("decided_at"))
            if timestamp is not None:
                timestamps.append(timestamp)
        if any(newer < older for newer, older in zip(timestamps, timestamps[1:])):
            errors.append(f"{path}: decision blocks are not newest-first")

    for record in records:
        path = record.path
        fields = record.fields
        for field in ("supersedes", "related"):
            values = fields.get(field, [])
            if isinstance(values, list):
                for related_id in values:
                    if not isinstance(related_id, str):
                        continue
                    if related_id not in by_id:
                        errors.append(f"{path}: {field} references unknown decision {related_id}")
                    elif field == "supersedes":
                        current_at = aware_timestamp(fields.get("decided_at"))
                        replaced_at = aware_timestamp(by_id[related_id].fields.get("decided_at"))
                        if current_at is not None and replaced_at is not None and current_at < replaced_at:
                            errors.append(
                                f"{path}: decision {fields.get('id')} cannot supersede later decision "
                                f"{related_id} at {replaced_at.isoformat()}"
                            )
        refs = fields.get("refs", [])
        reference_hashes = fields.get("reference_hashes", [])
        hash_by_reference: dict[str, str] = {}
        if isinstance(reference_hashes, list):
            for item in reference_hashes:
                reference, separator, digest = str(item).rpartition("=")
                if not separator or not reference or not re.fullmatch(r"[0-9a-f]{64}", digest):
                    errors.append(f"{path}: invalid reference SHA256 entry {item}")
                    continue
                if reference in hash_by_reference:
                    errors.append(f"{path}: duplicate reference SHA256 entry for {reference}")
                hash_by_reference[reference] = digest

        if isinstance(refs, list):
            ref_names = {str(ref) for ref in refs}
            for ref in refs:
                reference_name = str(ref)
                try:
                    reference_path = resolve_decision_reference(decision_root, reference_name)
                except ValueError as exc:
                    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", reference_name.strip()):
                        errors.append(
                            f"{path}: external reference {reference_name} is not valid in refs; "
                            "record URLs in source or a local ref file"
                        )
                    else:
                        errors.append(f"{path}: invalid reference {reference_name}: {exc}")
                    continue
                if not reference_path.is_file():
                    errors.append(f"{path}: missing reference {reference_name}")
                    continue
                expected_digest = hash_by_reference.get(reference_name)
                if expected_digest is None:
                    if "reference_hashes" in fields:
                        errors.append(f"{path}: missing reference SHA256 for {reference_name}")
                    elif require_integrity and record.format == "markdown":
                        errors.append(f"{path}: missing Reference SHA256 metadata for {reference_name}")
                    else:
                        warnings.append(f"{path}: reference {reference_name} has no integrity hash")
                    continue
                actual_digest = hashlib.sha256(reference_path.read_bytes()).hexdigest()
                if actual_digest != expected_digest:
                    errors.append(f"{path}: reference SHA256 does not match {reference_name}")

            for reference_name in hash_by_reference:
                if reference_name not in ref_names:
                    errors.append(f"{path}: reference SHA256 has no matching reference {reference_name}")

    graph: dict[str, list[str]] = {}
    for decision_id, record in by_id.items():
        values = record.fields.get("supersedes", [])
        graph[decision_id] = (
            [item for item in values if isinstance(item, str) and item in by_id]
            if isinstance(values, list) else []
        )
    visited: set[str] = set()
    for decision_id in graph:
        if decision_id in visited:
            continue
        visiting = {decision_id}
        trail = [decision_id]
        stack = [(decision_id, iter(graph[decision_id]))]
        while stack:
            node, parents = stack[-1]
            parent = next(parents, None)
            if parent is None:
                stack.pop()
                trail.pop()
                visiting.remove(node)
                visited.add(node)
            elif parent in visiting:
                errors.append(f"supersedes cycle detected: {' -> '.join(trail + [parent])}")
            elif parent not in visited:
                visiting.add(parent)
                trail.append(parent)
                stack.append((parent, iter(graph[parent])))
    return errors, warnings, records


def validate(
    root: Path,
    require_integrity: bool,
    require_history: bool = False,
    allow_unhashed_legacy: bool = False,
) -> tuple[list[str], list[str]]:
    errors, warnings, _ = scan_history(root, require_integrity, require_history, allow_unhashed_legacy)
    return errors, warnings


def validate_git_baseline(root: Path, baseline_ref: str, require_integrity: bool = False) -> list[str]:
    """Compare baseline, index, and worktree without trusting symlink contents."""

    cleaned_ref = baseline_ref.strip()
    if not cleaned_ref or cleaned_ref.startswith("-"):
        return [f"invalid Git baseline reference: {baseline_ref}"]

    try:
        listed = subprocess.run(
            ["git", "ls-tree", "-rz", cleaned_ref, "--", ".knowledges/decisions"],
            cwd=root,
            check=True,
            capture_output=True,
        )
        indexed = subprocess.run(
            ["git", "ls-files", "--stage", "-z", "--", ".knowledges/decisions"],
            cwd=root,
            check=True,
            capture_output=True,
        )
    except FileNotFoundError:
        return ["git executable is required for --against-git-ref"]
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", errors="replace").strip()
        return [f"could not read Git baseline {baseline_ref}: {detail or 'git ls-tree failed'}"]

    def git_blob(object_id: str) -> bytes:
        return subprocess.run(
            ["git", "cat-file", "blob", object_id], cwd=root, check=True, capture_output=True
        ).stdout

    def compare_bytes(relative_name: str, original: bytes, current: bytes, label: str) -> list[str]:
        if current == original:
            return []
        parts = Path(relative_name).parts
        is_period_file = (
            len(parts) == 3
            and parts[:2] == (".knowledges", "decisions")
            and re.fullmatch(r"\d{4}-\d{2}(?:-\d{2})?\.md", parts[2]) is not None
        )
        if not is_period_file or not current.endswith(original):
            return [f"{label} tracked decision history was edited or reordered: {relative_name}"]
        prefix_bytes = current[: len(current) - len(original)] if original else current
        try:
            prefix = prefix_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return [f"{label} new history is not valid UTF-8: {relative_name}"]
        added, parse_errors, _ = extract_records(Path(relative_name), prefix)
        if (
            not prefix
            or parse_errors
            or not added
            or any(record.format != "markdown" for record in added)
            or prefix_bytes.count(START_MARKER.encode()) != len(added)
            or prefix_bytes.count(END_MARKER.encode()) != len(added)
        ):
            return [f"{label} tracked decision history has invalid content prepended: {relative_name}"]
        return []

    def check_new_file(relative_name: str, content: bytes, label: str) -> list[str]:
        parts = Path(relative_name).parts
        if len(parts) < 3 or parts[:2] != (".knowledges", "decisions"):
            return []
        if "ref" in parts[2:] or not relative_name.endswith(".md"):
            return []
        try:
            records, parse_errors, _ = extract_records(Path(relative_name), content.decode("utf-8"))
        except UnicodeDecodeError:
            return [f"{label} new decision history is not valid UTF-8: {relative_name}"]
        if parse_errors or not records or any(record.format != "markdown" for record in records):
            return [f"{label} new history must use Markdown decision blocks: {relative_name}"]
        return []

    baseline: dict[str, tuple[str, str]] = {}
    index: dict[str, tuple[str, str]] = {}
    errors: list[str] = []
    for raw_entry in listed.stdout.split(b"\0"):
        if not raw_entry:
            continue
        metadata, separator, raw_name = raw_entry.partition(b"\t")
        fields = metadata.split()
        if not separator or len(fields) != 3:
            errors.append("could not parse Git baseline tree")
            continue
        baseline[raw_name.decode("utf-8", errors="surrogateescape")] = (
            fields[0].decode("ascii"), fields[2].decode("ascii")
        )
    for raw_entry in indexed.stdout.split(b"\0"):
        if not raw_entry:
            continue
        metadata, separator, raw_name = raw_entry.partition(b"\t")
        fields = metadata.split()
        if not separator or len(fields) != 3:
            errors.append("could not parse Git index")
            continue
        relative_name = raw_name.decode("utf-8", errors="surrogateescape")
        if fields[2] != b"0":
            errors.append(f"unmerged decision history in Git index: {relative_name}")
            continue
        index[relative_name] = (fields[0].decode("ascii"), fields[1].decode("ascii"))

    for relative_name, (baseline_mode, baseline_hash) in baseline.items():
        current_path = root / relative_name
        if not current_path.exists() and not current_path.is_symlink():
            errors.append(f"tracked decision history file was removed: {relative_name}")
        try:
            original = git_blob(baseline_hash)
            if current_path.exists() or current_path.is_symlink():
                file_mode = current_path.lstat().st_mode
                current_mode = "100755" if file_mode & 0o111 else "100644"
                if not stat.S_ISREG(file_mode) or current_mode != baseline_mode:
                    errors.append(f"tracked decision history file mode changed: {relative_name}")
                else:
                    errors.extend(compare_bytes(relative_name, original, current_path.read_bytes(), "worktree"))
            staged = index.get(relative_name)
            if staged is None:
                errors.append(f"staged tracked decision history file was removed: {relative_name}")
            else:
                staged_mode, staged_hash = staged
                if staged_mode != baseline_mode:
                    errors.append(f"staged tracked decision history file mode changed: {relative_name}")
                else:
                    errors.extend(compare_bytes(relative_name, original, git_blob(staged_hash), "staged"))
        except (OSError, subprocess.CalledProcessError) as exc:
            errors.append(f"could not compare tracked decision history file {relative_name}: {exc}")

    for relative_name, (_, staged_hash) in index.items():
        if relative_name not in baseline:
            try:
                errors.extend(check_new_file(relative_name, git_blob(staged_hash), "staged"))
            except subprocess.CalledProcessError as exc:
                errors.append(f"could not read staged decision history file {relative_name}: {exc}")
    try:
        for path in decision_files(root / ".knowledges" / "decisions"):
            relative_name = str(path.relative_to(root))
            if relative_name not in baseline:
                try:
                    errors.extend(check_new_file(relative_name, path.read_bytes(), "worktree"))
                except OSError as exc:
                    errors.append(f"could not read new decision history file {relative_name}: {exc}")
    except ValueError as exc:
        errors.append(str(exc))

    # The worktree can be valid while the index contains different bytes. Validate
    # the complete staged snapshot so a malformed decision cannot be committed
    # after a successful local check.
    if index:
        with tempfile.TemporaryDirectory(prefix="decision-history-index-") as temporary:
            staged_root = Path(temporary)
            for relative_name, (staged_mode, staged_hash) in index.items():
                if staged_mode not in {"100644", "100755"}:
                    errors.append(f"staged decision history file is not regular: {relative_name}")
                    continue
                target = staged_root / relative_name
                try:
                    target.resolve().relative_to(staged_root)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(git_blob(staged_hash))
                except (OSError, ValueError, subprocess.CalledProcessError) as exc:
                    errors.append(f"could not inspect staged decision history file {relative_name}: {exc}")
            staged_errors, _, _ = scan_history(staged_root, require_integrity=require_integrity)
            errors.extend(f"staged {error}" for error in staged_errors)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="project root; defaults to the current directory")
    parser.add_argument("--require-integrity", action="store_true")
    parser.add_argument("--require-history", action="store_true", help="fail when no decision history files exist")
    parser.add_argument(
        "--against-git-ref",
        help="compare tracked decision history to this trusted Git revision and allow valid prepends only",
    )
    args = parser.parse_args()
    root = Path(args.root).resolve()
    errors, warnings = validate(root, args.require_integrity, args.require_history)
    if args.against_git_ref:
        errors.extend(validate_git_baseline(root, args.against_git_ref, require_integrity=args.require_integrity))
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
