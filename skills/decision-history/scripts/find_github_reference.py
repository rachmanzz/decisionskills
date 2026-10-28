#!/usr/bin/env python3
"""Fetch a GitHub Markdown file, list headings, or extract selected sections."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from http.client import HTTPMessage
from pathlib import Path
from typing import IO, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
DECISION_ID_PATTERN = re.compile(r"^DEC-\d{8}-\d{6}(?:-(?:0[2-9]|[1-9]\d+))?$")
HEADING_PATTERN = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$")
FENCE_PATTERN = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
SETEXT_PATTERN = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
MAX_MARKDOWN_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class Heading:
    line: int
    level: int
    title: str
    slug: str


def normalize_git_ref(git_ref: str) -> str:
    normalized = git_ref.strip().strip("/")
    if (
        not normalized
        or normalized.startswith("-")
        or "`" in normalized
        or any(ord(ch) < 32 or ord(ch) == 127 for ch in normalized)
        or ".." in normalized
        or any(part in {"", ".", ".."} for part in normalized.split("/"))
    ):
        raise ValueError("git-ref must be a valid Git branch, tag, or commit without traversal or backticks")
    return normalized


def normalize_path(path: str) -> str:
    normalized = path.strip().lstrip("/")
    if (
        not normalized
        or "`" in normalized
        or any(ord(ch) < 32 or ord(ch) == 127 for ch in normalized)
        or any(part in {"", ".", ".."} for part in normalized.split("/"))
    ):
        raise ValueError("path must be a non-empty repository-relative file path")
    if not normalized.lower().endswith(".md"):
        raise ValueError("path must point to a Markdown file ending in .md")
    return normalized


def normalize_selector(selector: str) -> str:
    normalized = selector.strip().lstrip("#").lower()
    if not normalized or "`" in normalized or any(ord(ch) < 32 or ord(ch) == 127 for ch in normalized):
        raise ValueError("section IDs must not be empty or contain backticks")
    return normalized


def github_slug(title: str) -> str:
    """Approximate GitHub's Markdown heading slug for ordinary headings."""

    title = re.sub(r"`([^`]*)`", r"\1", title)
    title = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", title)
    title = re.sub(r"<[^>]+>", "", title)
    title = unicodedata.normalize("NFKC", title).lower()
    title = re.sub(r"[^\w\s-]", "", title, flags=re.UNICODE)
    return re.sub(r"[-\s]+", "-", title).strip("-")


def parse_headings(text: str) -> list[Heading]:
    headings: list[Heading] = []
    occurrences: dict[str, int] = {}
    used_slugs: set[str] = set()
    fenced = False
    fence_character = ""
    fence_length = 0
    inside_comment = False
    paragraph_lines: list[tuple[int, str]] = []

    def add_heading(line_number: int, level: int, title: str) -> None:
        base_slug = github_slug(title)
        if not base_slug:
            return
        occurrence = occurrences.get(base_slug, 0)
        slug = base_slug if occurrence == 0 else f"{base_slug}-{occurrence}"
        while slug in used_slugs:
            occurrence += 1
            slug = f"{base_slug}-{occurrence}"
        occurrences[base_slug] = occurrence + 1
        used_slugs.add(slug)
        headings.append(Heading(line_number, level, title, slug))

    for line_number, raw_line in enumerate(text.splitlines()):
        line = raw_line
        if not fenced:
            visible = ""
            cursor = 0
            while cursor < len(line):
                if inside_comment:
                    end = line.find("-->", cursor)
                    if end < 0:
                        break
                    inside_comment = False
                    cursor = end + 3
                else:
                    start = line.find("<!--", cursor)
                    if start < 0:
                        visible += line[cursor:]
                        break
                    visible += line[cursor:start]
                    inside_comment = True
                    cursor = start + 4
            line = visible
        if inside_comment:
            paragraph_lines = []
            continue
        fence = FENCE_PATTERN.match(line)
        if fence:
            paragraph_lines = []
            marker = fence.group(1)[0]
            if not fenced:
                fenced = True
                fence_character = marker
                fence_length = len(fence.group(1))
            elif (
                marker == fence_character
                and len(fence.group(1)) >= fence_length
                and not fence.group(2).strip()
            ):
                fenced = False
            continue
        if fenced:
            paragraph_lines = []
            continue
        setext = SETEXT_PATTERN.match(line)
        if setext:
            if paragraph_lines:
                title = " ".join(part for _, part in paragraph_lines)
                add_heading(paragraph_lines[0][0], 1 if setext.group(1)[0] == "=" else 2, title)
            paragraph_lines = []
            continue
        match = HEADING_PATTERN.match(line)
        if match:
            add_heading(line_number, len(match.group(1)), match.group(2).strip())
            paragraph_lines = []
            continue
        if line.strip() and not line.startswith("    ") and not re.match(r"^\s*[-*+>]\s", line):
            paragraph_lines.append((line_number, line.strip()))
        else:
            paragraph_lines = []
    return headings


def heading_listing(text: str) -> str:
    """Render headings and their GitHub IDs without returning document content."""

    headings = parse_headings(text)
    if not headings:
        return "No Markdown headings found.\n"
    lines = ["# GitHub Markdown Headings", ""]
    lines.extend(
        f"- Line {heading.line + 1}: `{heading.slug}` — {'#' * heading.level} {heading.title}"
        for heading in headings
    )
    return "\n".join(lines) + "\n"


def find_heading(headings: Iterable[Heading], selector: str, *, suffix: bool = False) -> Heading:
    normalized = normalize_selector(selector)
    matches = [heading for heading in headings if heading.slug == normalized]
    if not matches and suffix:
        matches = [heading for heading in headings if heading.slug.endswith(f"-{normalized}")]
    if not matches:
        raise ValueError(f"section ID not found: {selector}")
    if len(matches) > 1:
        raise ValueError(f"section ID is ambiguous: {selector}")
    return matches[0]


def section_end(heading: Heading, headings: list[Heading], line_count: int) -> int:
    for candidate in headings:
        if candidate.line > heading.line and candidate.level <= heading.level:
            return candidate.line
    return line_count


def extract_sections(text: str, selectors: list[str], excludes: list[str]) -> str:
    lines = text.splitlines()
    headings = parse_headings(text)
    selected_headings = [find_heading(headings, selector) for selector in selectors]
    selected_ranges = sorted(
        (heading.line, section_end(heading, headings, len(lines))) for heading in selected_headings
    )
    merged_ranges: list[tuple[int, int]] = []
    for start, end in selected_ranges:
        if merged_ranges and start < merged_ranges[-1][1]:
            previous_start, previous_end = merged_ranges[-1]
            merged_ranges[-1] = (previous_start, max(previous_end, end))
        else:
            merged_ranges.append((start, end))
    selected_ranges = merged_ranges

    excluded_ranges: list[tuple[int, int]] = []
    for selector in excludes:
        normalized = normalize_selector(selector)
        nested_headings = [
            heading for heading in headings
            if any(start < heading.line < end for start, end in selected_ranges)
        ]
        matches = [heading for heading in nested_headings if heading.slug == normalized]
        if not matches:
            matches = [
                heading for heading in nested_headings
                if github_slug(heading.title) == normalized or heading.slug.endswith(f"-{normalized}")
            ]
        if not matches:
            raise ValueError(f"excluded section is not inside a selected section: {selector}")
        if len(matches) > 1:
            raise ValueError(f"excluded section ID is ambiguous within selected sections: {selector}")
        excluded_heading = matches[0]
        matching_ranges = [
            (start, end)
            for start, end in selected_ranges
            if start <= excluded_heading.line < end and excluded_heading.line != start
        ]
        if not matching_ranges:
            raise ValueError(f"excluded section is not inside a selected section: {selector}")
        outer_end = min(end for _, end in matching_ranges)
        excluded_ranges.append(
            (excluded_heading.line, min(section_end(excluded_heading, headings, len(lines)), outer_end))
        )

    parts: list[str] = []
    for start, end in sorted(selected_ranges):
        removed = [
            (excluded_start, excluded_end)
            for excluded_start, excluded_end in excluded_ranges
            if start <= excluded_start < end
        ]
        cursor = start
        chunks: list[str] = []
        for excluded_start, excluded_end in sorted(removed):
            if cursor < excluded_start:
                chunks.append("\n".join(lines[cursor:excluded_start]))
            cursor = max(cursor, excluded_end)
        if cursor < end:
            chunks.append("\n".join(lines[cursor:end]))
        part = "\n".join(chunk for chunk in chunks if chunk.strip()).strip()
        if part:
            parts.append(part)
    if not parts:
        raise ValueError("selected sections are empty after exclusions")
    return "\n\n".join(parts) + "\n"


def raw_url(repository: str, git_ref: str, path: str) -> str:
    return (
        f"https://raw.githubusercontent.com/{repository}/{quote(git_ref, safe='/')}"
        f"/{quote(path, safe='/')}"
    )


def github_url(repository: str, git_ref: str, path: str, section_id: str) -> str:
    return (
        f"https://github.com/{repository}/blob/{quote(git_ref, safe='/')}"
        f"/{quote(path, safe='/')}#{normalize_selector(section_id)}"
    )


class OriginBoundAuthorizationRedirect(HTTPRedirectHandler):
    """Do not forward a GitHub token when a redirect changes the URL origin."""

    def redirect_request(self, req: Request, fp: IO[bytes], code: int, msg: str,
                         headers: HTTPMessage, newurl: str) -> Request | None:
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            old = urlsplit(req.full_url)
            new = urlsplit(newurl)
            if (old.scheme, old.hostname, old.port) != (new.scheme, new.hostname, new.port):
                redirected.remove_header("Authorization")
        return redirected


def fetch_markdown(url: str, timeout: float) -> str:
    request = Request(url, headers={"User-Agent": "decision-history-github-reference"})
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        opener = build_opener(OriginBoundAuthorizationRedirect()) if token else None
        with (opener.open(request, timeout=timeout) if opener else urlopen(request, timeout=timeout)) as response:
            payload = response.read(MAX_MARKDOWN_BYTES + 1)
            if len(payload) > MAX_MARKDOWN_BYTES:
                raise RuntimeError(f"GitHub file exceeds maximum size ({MAX_MARKDOWN_BYTES} bytes): {url}")
            return payload.decode("utf-8")
    except HTTPError as exc:
        if exc.code in {401, 403, 404}:
            raise RuntimeError(
                f"GitHub could not fetch {url}; check that the repository is public or set GITHUB_TOKEN"
            ) from exc
        raise RuntimeError(f"GitHub returned HTTP {exc.code} for {url}") from exc
    except URLError as exc:
        raise RuntimeError(f"could not reach GitHub: {exc.reason}") from exc
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"GitHub file is not valid UTF-8: {url}") from exc


def build_snapshot(
    repository: str,
    git_ref: str,
    path: str,
    selectors: list[str],
    excludes: list[str],
    extracted: str,
    decision_id: str | None,
) -> str:
    digest = hashlib.sha256(extracted.encode("utf-8")).hexdigest()
    retrieved_at = datetime.now().astimezone().replace(microsecond=0).isoformat()
    source = github_url(repository, git_ref, path, selectors[0])
    lines = [
        "# GitHub Reference",
        "",
        "- Type: `GitHub Markdown section`",
        f"- Decision: `{decision_id}`" if decision_id else "- Decision: none",
        f"- Repository: `{repository}`",
        f"- Revision: `{git_ref}`",
        f"- File: `{path}`",
        f"- Sections: {', '.join(f'`{selector}`' for selector in selectors)}",
        f"- Excluded sections: {', '.join(f'`{selector}`' for selector in excludes) if excludes else 'none'}",
        f"- Retrieved at: `{retrieved_at}`",
        f"- URL: {source}",
        f"- Raw URL: {raw_url(repository, git_ref, path)}",
        f"- Content SHA256: `{digest}`",
        "",
        "## Context",
        "",
        "This reference preserves the selected GitHub Markdown section as supporting context for the decision.",
        "",
        "## Evidence",
        "",
        extracted.rstrip(),
        "",
    ]
    return "\n".join(lines)


def reference_output_path(root: Path, output: str, decision_id: str | None) -> Path:
    project_root = root.resolve()
    decision_root = (project_root / ".knowledges" / "decisions").resolve()
    try:
        decision_root.relative_to(project_root)
    except ValueError as exc:
        raise ValueError("decision directory must stay inside the project root") from exc
    relative_output = Path(output) if output else Path("ref") / f"{decision_id}-github.md"
    if relative_output.is_absolute():
        raise ValueError("output must be a relative path inside .knowledges/decisions/ref/")
    if relative_output.suffix.lower() != ".md":
        raise ValueError("output must be a Markdown file ending in .md")
    candidate = (decision_root / relative_output).resolve()
    reference_root = (decision_root / "ref").resolve()
    try:
        reference_root.relative_to(decision_root)
    except ValueError as exc:
        raise ValueError("reference directory must stay inside .knowledges/decisions/") from exc
    try:
        candidate.relative_to(reference_root)
    except ValueError as exc:
        raise ValueError("output must be inside .knowledges/decisions/ref/") from exc
    if candidate.exists():
        raise FileExistsError(f"reference already exists; choose a new path: {candidate}")
    return candidate


def create_reference_snapshot(path: Path, content: str) -> None:
    """Create a new snapshot without allowing a concurrent writer to replace it."""

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
    except FileExistsError as exc:
        raise FileExistsError(f"reference already exists; choose a new path: {path}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="project root; defaults to the current directory")
    parser.add_argument("--repo", required=True, help="GitHub repository in owner/name form")
    parser.add_argument("--path", required=True, help="Markdown file path inside the repository")
    parser.add_argument("--git-ref", default="main", help="Git branch, tag, or commit; defaults to main")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--id", dest="section_ids", action="append", help="heading ID; repeatable")
    mode.add_argument("--list-headings", action="store_true", help="list headings and GitHub IDs")
    mode.add_argument("--all", dest="all_content", action="store_true", help="print the complete Markdown file")
    parser.add_argument("--exclude", action="append", default=[], help="nested heading ID to exclude; repeatable")
    parser.add_argument("--decision-id", help="decision ID to include in metadata and the default output name")
    parser.add_argument(
        "--output",
        help="optional relative Markdown path; must end in .md and be inside .knowledges/decisions/ref/",
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="network timeout in seconds")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if not REPOSITORY_PATTERN.fullmatch(args.repo):
            raise ValueError("repo must use owner/name form")
        if args.timeout <= 0:
            raise ValueError("timeout must be positive")
        if args.decision_id and not DECISION_ID_PATTERN.fullmatch(args.decision_id):
            raise ValueError(
                "decision-id must match DEC-YYYYMMDD-HHMMSS or "
                "DEC-YYYYMMDD-HHMMSS-NN (two or more suffix digits)"
            )
        path = normalize_path(args.path)
        git_ref = normalize_git_ref(args.git_ref)
        if args.list_headings or args.all_content:
            if args.decision_id or args.output:
                raise ValueError("--list-headings and --all are read-only and cannot write a reference")
            if args.exclude:
                raise ValueError("--exclude requires --id")
        selectors = [normalize_selector(selector) for selector in (args.section_ids or [])]
        excludes = [normalize_selector(selector) for selector in args.exclude]
        source = raw_url(args.repo, git_ref, path)
        markdown = fetch_markdown(source, args.timeout)
        if args.list_headings:
            print(heading_listing(markdown), end="")
            return 0
        if args.all_content:
            print(markdown, end="")
            return 0
        extracted = extract_sections(markdown, selectors, excludes)
        snapshot = build_snapshot(
            args.repo,
            git_ref,
            path,
            selectors,
            excludes,
            extracted,
            args.decision_id,
        )
        if args.output or args.decision_id:
            root = Path(args.root).resolve()
            output = reference_output_path(root, args.output or "", args.decision_id)
            relative = output.relative_to((root / ".knowledges" / "decisions").resolve())
            create_reference_snapshot(output, snapshot)
            print(f"wrote reference: {output}")
            print(f"use with record_decision.py: --ref {relative}")
        else:
            print(snapshot, end="")
        return 0
    except (FileExistsError, OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
