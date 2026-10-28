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

fcntl: Any = None
try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised only on non-POSIX hosts
    pass
else:
    fcntl = _fcntl


ID_PATTERN = re.compile(r"^DEC-\d{8}-\d{6}(?:-\d{2})?$")
ID_SCAN_PATTERN = re.compile(r"DEC-\d{8}-\d{6}(?:-\d{2})?")
START_MARKER = "<!-- decision:start -->"


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


def existing_ids(decision_root: Path) -> set[str]:
    ids: set[str] = set()
    if not decision_root.exists():
        return ids
    for path in decision_root.rglob("*.md"):
        if "ref" in path.relative_to(decision_root).parts:
            continue
        ids.update(ID_SCAN_PATTERN.findall(path.read_text(encoding="utf-8")))
    return ids


def choose_id(timestamp: datetime, decision_root: Path, requested: str | None) -> str:
    used = existing_ids(decision_root)
    if requested:
        if not ID_PATTERN.fullmatch(requested):
            raise ValueError("id must match DEC-YYYYMMDD-HHMMSS or DEC-YYYYMMDD-HHMMSS-NN")
        if requested in used:
            raise FileExistsError(f"decision ID already exists: {requested}")
        return requested

    base = f"DEC-{timestamp:%Y%m%d-%H%M%S}"
    if base not in used:
        return base
    suffix = 2
    while f"{base}-{suffix:02d}" in used:
        suffix += 1
    return f"{base}-{suffix:02d}"


def build_entry(args: argparse.Namespace, decision_id: str, timestamp: datetime) -> str:
    title = args.title.strip()
    sources = args.source or ["conversation"]
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


def lock_path(path: Path) -> Path:
    key = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()
    directory = Path(tempfile.gettempdir()) / "decision-history-locks"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{key}.lock"


@contextlib.contextmanager
def history_lock(path: Path, timeout: float) -> Iterator[None]:
    """Serialize helper writes for one history path on the current host."""

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
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
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
    parser.add_argument("--ref", action="append", default=[])
    parser.add_argument("--source", action="append", default=[])
    parser.add_argument("--timestamp", help="ISO 8601 timestamp with timezone; defaults to now")
    parser.add_argument("--id", help="explicit decision ID; normally generated from the timestamp")
    parser.add_argument("--lock-timeout", type=float, default=30.0, help="seconds to wait for a concurrent writer")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if not args.decision.strip():
            raise ValueError("decision must not be empty")
        if args.lock_timeout < 0:
            raise ValueError("lock-timeout must not be negative")
        timestamp = parse_timestamp(args.timestamp)
        project_root = Path(args.root).resolve()
        decision_root = project_root / ".knowledges" / "decisions"
        decision_root.mkdir(parents=True, exist_ok=True)
        filename = timestamp.strftime("%Y-%m.md" if args.period == "month" else "%Y-%m-%d.md")
        path = decision_root / filename
        with history_lock(path, args.lock_timeout):
            decision_id = choose_id(timestamp, decision_root, args.id)
            existing = path.read_text(encoding="utf-8") if path.exists() else ""
            if existing.strip() and START_MARKER not in existing:
                raise ValueError(
                    f"period file exists in legacy format: {path}; preserve it and choose a new period file "
                    "or migrate it manually"
                )
            combined = build_entry(args, decision_id, timestamp) + (
                "\n" + existing.lstrip("\n") if existing.strip() else ""
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
