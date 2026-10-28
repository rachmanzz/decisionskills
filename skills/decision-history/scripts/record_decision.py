#!/usr/bin/env python3
"""Create a new timestamped decision without overwriting an existing record."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path


ID_PATTERN = re.compile(r"^DEC-\d{8}-\d{6}(?:-\d{2})?$")


def yaml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def yaml_array(values: list[str]) -> str:
    return json.dumps(values, ensure_ascii=False)


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
        for line in path.read_text(encoding="utf-8").splitlines()[:24]:
            if line.startswith("id:"):
                ids.add(line.split(":", 1)[1].strip().strip('"\''))
                break
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


def markdown_body(args: argparse.Namespace, title: str) -> str:
    lines = [f"# {title}", "", "## Decision", "", args.decision.strip(), ""]
    if args.rationale:
        lines += ["## Rationale", "", args.rationale.strip(), ""]
    if args.consequence:
        lines += ["## Consequences", ""]
        lines += [f"- {item.strip()}" for item in args.consequence]
        lines.append("")
    if args.agent_action:
        lines += ["## Agent action", "", args.agent_action.strip(), ""]
    return "\n".join(lines).rstrip() + "\n"


def build_document(args: argparse.Namespace, decision_id: str, timestamp: datetime) -> str:
    title = args.title.strip()
    sources = args.source or ["conversation"]
    body = markdown_body(args, title)
    frontmatter = "\n".join(
        [
            "---",
            f"id: {yaml_string(decision_id)}",
            f"title: {yaml_string(title)}",
            f"decided_at: {yaml_string(timestamp.isoformat())}",
            f"scope: {yaml_string(args.scope)}",
            f"tags: {yaml_array(args.tag)}",
            f"supersedes: {yaml_array(args.supersedes)}",
            f"related: {yaml_array(args.related)}",
            f"refs: {yaml_array(args.ref)}",
            f"source: {yaml_array(sources)}",
            'integrity_sha256: ""',
            "---",
            "",
        ]
    )
    document = frontmatter + body
    digest = hashlib.sha256(document.encode("utf-8")).hexdigest()
    return document.replace('integrity_sha256: ""', f'integrity_sha256: "{digest}"', 1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="project root; defaults to the current directory")
    parser.add_argument("--title", required=True)
    parser.add_argument("--decision", required=True)
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
        timestamp = parse_timestamp(args.timestamp)
        project_root = Path(args.root).resolve()
        decision_root = project_root / ".knowledges" / "decisions"
        decision_id = choose_id(timestamp, decision_root, args.id)
        month_dir = decision_root / timestamp.strftime("%Y-%m")
        month_dir.mkdir(parents=True, exist_ok=True)
        path = month_dir / f"{decision_id}.md"
        document = build_document(args, decision_id, timestamp)
        with path.open("x", encoding="utf-8") as output:
            output.write(document)
    except (ValueError, FileExistsError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"created {path}")
    print(f"id: {decision_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
