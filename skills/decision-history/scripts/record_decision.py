#!/usr/bin/env python3
"""Prepend a new Markdown-only decision block to a month or date history file."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import os
import re
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from validate_decisions import (
    Record,
    check_decision_root,
    decision_content,
    decision_files,
    extract_records,
    integrity_material,
    resolve_decision_reference,
)

fcntl: Any = None
try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised only on non-POSIX hosts
    pass
else:
    fcntl = _fcntl


ID_PATTERN = re.compile(r"^DEC-\d{8}-\d{6}(?:-(?:0[2-9]|[1-9]\d+))?$")
START_MARKER = "<!-- decision:start -->"
END_MARKER = "<!-- decision:end -->"


def inline(value: str) -> str:
    return f"`{value}`"


def list_value(values: list[str]) -> str:
    if not values:
        return "none"
    return ", ".join(inline(value) for value in values)


def parse_timestamp(value: str | None) -> datetime:
    if value is None:
        return datetime.now().astimezone().replace(microsecond=0)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("timestamp must be ISO 8601, for example 2026-10-28T15:30:00+07:00") from exc
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.replace(microsecond=0)


def existing_records(decision_root: Path) -> dict[str, Record]:
    by_id: dict[str, Record] = {}
    if not decision_root.exists():
        return by_id
    for path in decision_files(decision_root):
        records, _, _ = extract_records(path)
        for record in records:
            decision_id = record.fields.get("id")
            if isinstance(decision_id, str) and ID_PATTERN.fullmatch(decision_id):
                by_id.setdefault(decision_id, record)
    return by_id


def choose_id(timestamp: datetime, used: set[str], requested: str | None) -> str:
    base = f"DEC-{timestamp:%Y%m%d-%H%M%S}"
    if requested:
        if not ID_PATTERN.fullmatch(requested):
            raise ValueError("id must match DEC-YYYYMMDD-HHMMSS or DEC-YYYYMMDD-HHMMSS-NN (two or more suffix digits)")
        if requested in used:
            raise FileExistsError(f"decision ID already exists: {requested}")
        if requested != base and not requested.startswith(base + "-"):
            raise ValueError("id must match the timestamp's local date and time")
        return requested

    if base not in used:
        return base
    suffix = 2
    while f"{base}-{suffix:02d}" in used:
        suffix += 1
    return f"{base}-{suffix:02d}"


def validate_reference_inputs(references: list[str], decision_root: Path) -> None:
    seen: set[str] = set()
    for reference in references:
        try:
            path = resolve_decision_reference(decision_root, reference)
        except ValueError as exc:
            raise ValueError(f"invalid --ref {reference}: {exc}; use --source or a local ref file") from exc
        if reference in seen:
            raise ValueError(f"duplicate --ref value: {reference}")
        seen.add(reference)
        if not path.is_file():
            raise ValueError(f"invalid --ref {reference}: reference file must already exist")


def validate_inputs(args: argparse.Namespace) -> None:
    single_line_fields = {
        "title": args.title,
        "scope": args.scope,
        **{f"tag[{index}]": value for index, value in enumerate(args.tag)},
        **{f"supersedes[{index}]": value for index, value in enumerate(args.supersedes)},
        **{f"related[{index}]": value for index, value in enumerate(args.related)},
        **{f"ref[{index}]": value for index, value in enumerate(args.ref)},
        **{f"source[{index}]": value for index, value in enumerate(args.source)},
        **{f"consequence[{index}]": value for index, value in enumerate(args.consequence)},
    }
    for label, value in single_line_fields.items():
        if not value.strip():
            raise ValueError(f"{label} must not be empty")
        if value != value.strip():
            raise ValueError(f"{label} must not have leading or trailing whitespace")
        if "\n" in value or "\r" in value:
            raise ValueError(f"{label} must be a single line")
        if "`" in value:
            raise ValueError(f"{label} must not contain backticks")
        if START_MARKER in value or END_MARKER in value:
            raise ValueError(f"{label} must not contain decision block markers")

    for label, value in {
        "decision": args.decision,
        "rationale": args.rationale,
        "agent-action": args.agent_action,
    }.items():
        if label == "decision" and not value.strip():
            raise ValueError("decision must not be empty")
        if value and (START_MARKER in value or END_MARKER in value):
            raise ValueError(f"{label} must not contain decision block markers")


def reference_hashes(references: list[str], decision_root: Path) -> list[str]:
    hashes: list[str] = []
    for reference in references:
        reference_path = resolve_decision_reference(decision_root, reference)
        if reference_path.is_file():
            digest = hashlib.sha256(reference_path.read_bytes()).hexdigest()
            hashes.append(f"{reference}={digest}")
    return hashes


def build_entry(args: argparse.Namespace, decision_id: str, timestamp: datetime, decision_root: Path) -> str:
    title = args.title.strip()
    sources = args.source or ["conversation"]
    reference_digests = reference_hashes(args.ref, decision_root)
    lines = [
        START_MARKER,
        f"## {title}",
        "",
        f"- ID: {inline(decision_id)}",
        f"- Decided at: {inline(timestamp.isoformat())}",
        f"- Scope: {inline(args.scope)}",
        f"- Tags: {list_value(args.tag)}",
        f"- Supersedes: {list_value(args.supersedes)}",
        f"- Related: {list_value(args.related)}",
        f"- References: {list_value(args.ref)}",
        f"- Reference SHA256: {list_value(reference_digests)}",
        f"- Source: {list_value(sources)}",
        "- Integrity SHA256: ``",
        "",
        "### Decision",
        "",
        args.decision.strip(),
        "",
    ]
    if args.rationale:
        lines += ["### Rationale", "", args.rationale.strip(), ""]
    if args.consequence:
        lines += ["### Consequences", ""]
        lines += [f"- {item.strip()}" for item in args.consequence]
        lines.append("")
    if args.agent_action:
        lines += ["### Agent action", "", args.agent_action.strip(), ""]
    lines += ["<!-- decision:end -->", ""]
    entry = "\n".join(lines)
    digest = hashlib.sha256(entry.encode("utf-8")).hexdigest()
    return entry.replace("- Integrity SHA256: ``", f"- Integrity SHA256: `{digest}`", 1)


def validate_candidate_entry(
    entry: str, path: Path, args: argparse.Namespace, decision_id: str, timestamp: datetime
) -> None:
    records, errors, _ = extract_records(path, entry)
    if errors or len(records) != 1 or records[0].format != "markdown":
        raise ValueError(f"new decision block is malformed: {'; '.join(errors) or 'expected one Markdown block'}")
    record = records[0]
    if not ID_PATTERN.fullmatch(decision_id):
        raise ValueError(f"new decision ID has an invalid format: {decision_id}")
    expected = {
        "id": decision_id,
        "title": args.title,
        "decided_at": timestamp.isoformat(),
        "scope": args.scope,
        "tags": args.tag,
        "supersedes": args.supersedes,
        "related": args.related,
        "refs": args.ref,
        "source": args.source or ["conversation"],
    }
    if any(record.fields.get(field) != value for field, value in expected.items()):
        raise ValueError("new decision metadata does not round-trip through the validator")
    if not decision_content(record):
        raise ValueError("new decision has an empty or missing Decision section")
    digest = hashlib.sha256(integrity_material(record.raw, record.format).encode("utf-8")).hexdigest()
    if record.fields.get("integrity_sha256") != digest:
        raise ValueError("new decision integrity hash does not match its content")


def lock_path(path: Path) -> Path:
    key = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()
    uid = os.getuid() if hasattr(os, "getuid") else "default"
    directory = Path(tempfile.gettempdir()) / f"decision-history-locks-{uid}"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    return directory / f"{key}.lock"


@contextlib.contextmanager
def history_lock(path: Path, timeout: float) -> Iterator[None]:
    """Serialize helper writes for one project on the current host."""

    path_to_lock = lock_path(path)
    deadline = time.monotonic() + timeout

    if fcntl is not None:
        with path_to_lock.open("a+", encoding="utf-8") as handle:
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"could not acquire history lock within {timeout:g}s: {path}")
                    time.sleep(0.05)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return

    # Safe fallback for hosts without POSIX flock: fail on timeout rather than
    # risk concurrent writes. A stale fallback lock requires manual cleanup.
    acquired = False
    try:
        while not acquired:
            try:
                descriptor = os.open(path_to_lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(descriptor)
                acquired = True
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"could not acquire history lock within {timeout:g}s: {path}")
                time.sleep(0.05)
        yield
    finally:
        if acquired:
            try:
                path_to_lock.unlink()
            except FileNotFoundError:
                pass


def atomic_write(path: Path, content: str) -> None:
    existing_mode: int | None = None
    if path.exists():
        existing_mode = path.stat().st_mode & 0o777
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        if existing_mode is not None:
            os.chmod(temporary_path, existing_mode)
        else:
            current_umask = os.umask(0)
            os.umask(current_umask)
            os.chmod(temporary_path, 0o666 & ~current_umask)
        os.replace(temporary_path, path)
        if hasattr(os, "O_DIRECTORY"):
            directory_descriptor = os.open(path.parent, os.O_DIRECTORY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="project root; defaults to the current directory")
    parser.add_argument("--period", choices=("month", "date"), default="month")
    parser.add_argument("--title", required=True)
    parser.add_argument("--decision", required=True, help="clear, settled decision statement")
    parser.add_argument("--rationale")
    parser.add_argument("--consequence", action="append", default=[])
    parser.add_argument("--agent-action")
    parser.add_argument("--scope", default="project")
    parser.add_argument("--tag", action="append", default=[])
    parser.add_argument("--supersedes", action="append", default=[])
    parser.add_argument("--related", action="append", default=[])
    parser.add_argument(
        "--ref",
        action="append",
        default=[],
        help="existing local reference path relative to .knowledges/decisions/",
    )
    parser.add_argument("--source", action="append", default=[], help="decision origin or external URL")
    parser.add_argument("--timestamp", help="ISO 8601 timestamp with timezone; defaults to now")
    parser.add_argument("--id", help="explicit decision ID; normally generated from the timestamp")
    parser.add_argument("--lock-timeout", type=float, default=30.0, help="seconds to wait for a concurrent writer")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        validate_inputs(args)
        if args.lock_timeout < 0:
            raise ValueError("lock-timeout must not be negative")
        timestamp = parse_timestamp(args.timestamp)
        project_root = Path(args.root).resolve()
        decision_root = project_root / ".knowledges" / "decisions"
        check_decision_root(project_root)
        validate_reference_inputs(args.ref, decision_root)
        decision_root.mkdir(parents=True, exist_ok=True)
        filename = timestamp.strftime("%Y-%m.md" if args.period == "month" else "%Y-%m-%d.md")
        path = decision_root / filename
        with history_lock(decision_root / ".history", args.lock_timeout):
            known_records = existing_records(decision_root)
            decision_id = choose_id(timestamp, set(known_records), args.id)
            existing = path.read_text(encoding="utf-8") if path.exists() else ""
            if existing.strip() and START_MARKER not in existing:
                raise ValueError(
                    f"period file exists in legacy format: {path}; preserve it and choose a new period file "
                    "or migrate it manually"
                )
            period_records, parse_errors, _ = extract_records(path, existing) if existing else ([], [], [])
            if parse_errors:
                raise ValueError(
                    f"period file contains malformed decision history: {path}; "
                    "preserve it and report the validator errors"
                )
            if period_records:
                newest_value = period_records[0].fields.get("decided_at")
                if newest_value:
                    newest_timestamp = datetime.fromisoformat(str(newest_value))
                    if newest_timestamp.tzinfo is None:
                        raise ValueError(f"newest decision in {path} has a timestamp without a timezone")
                    if timestamp < newest_timestamp:
                        raise ValueError(
                            f"timestamp {timestamp.isoformat()} is older than the newest decision "
                            f"in {path} ({newest_timestamp.isoformat()}); choose a current timestamp or another period"
                        )

            for field_name, values in (("supersedes", args.supersedes), ("related", args.related)):
                for related_id in values:
                    if related_id not in known_records:
                        raise ValueError(f"{field_name} references unknown decision {related_id}")
                    if related_id == decision_id and field_name == "supersedes":
                        raise ValueError("a decision cannot supersede itself")
                    if field_name == "supersedes":
                        parent_value = known_records[related_id].fields.get("decided_at")
                        try:
                            parent_timestamp = datetime.fromisoformat(str(parent_value))
                        except ValueError:
                            parent_timestamp = None
                        if parent_timestamp is not None and parent_timestamp.tzinfo is not None:
                            if timestamp < parent_timestamp:
                                raise ValueError(
                                    f"decision at {timestamp.isoformat()} cannot supersede later decision "
                                    f"{related_id} at {parent_timestamp.isoformat()}"
                                )
            entry = build_entry(args, decision_id, timestamp, decision_root)
            validate_candidate_entry(entry, path, args, decision_id, timestamp)
            combined = entry + (
                "\n" + existing if existing else ""
            )
            atomic_write(path, combined)
    except (ValueError, FileExistsError, TimeoutError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"prepended {path}")
    print(f"id: {decision_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
