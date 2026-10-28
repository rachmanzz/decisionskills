#!/usr/bin/env python3
"""Prepend a new Markdown-only decision block to a month or date history file."""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from datetime import datetime
from pathlib import Path


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


def prepend_entry(path: Path, entry: str) -> None:
    if not path.exists():
        with path.open("x", encoding="utf-8") as output:
            output.write(entry)
        return

    existing = path.read_text(encoding="utf-8")
    if existing.strip() and START_MARKER not in existing:
        raise ValueError(
            f"period file exists in legacy format: {path}; preserve it and choose a new period file or migrate it manually"
        )
    combined = entry + ("\n" + existing.lstrip("\n") if existing.strip() else "")
    path.write_text(combined, encoding="utf-8")


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
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if not args.decision.strip():
            raise ValueError("decision must not be empty")
        timestamp = parse_timestamp(args.timestamp)
        project_root = Path(args.root).resolve()
        decision_root = project_root / ".knowledges" / "decisions"
        decision_id = choose_id(timestamp, decision_root, args.id)
        decision_root.mkdir(parents=True, exist_ok=True)
        filename = timestamp.strftime("%Y-%m.md" if args.period == "month" else "%Y-%m-%d.md")
        path = decision_root / filename
        prepend_entry(path, build_entry(args, decision_id, timestamp))
    except (ValueError, FileExistsError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"prepended {path}")
    print(f"id: {decision_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
