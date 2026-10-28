from __future__ import annotations

import concurrent.futures
import hashlib
import importlib.util
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from email.message import Message
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "skills" / "decision-history" / "scripts"
RECORD = SCRIPTS / "record_decision.py"
VALIDATE = SCRIPTS / "validate_decisions.py"
LIST = SCRIPTS / "list_decisions.py"
MIGRATE = SCRIPTS / "migrate_decisions.py"
FIND_GITHUB = SCRIPTS / "find_github_reference.py"
SKILL = PROJECT_ROOT / "skills" / "decision-history" / "SKILL.md"
COMPANION_TOOLS = PROJECT_ROOT / "skills" / "decision-history" / "references" / "companion-tools.md"
PLUGIN_MANIFEST = PROJECT_ROOT / "plugin.json"
SAFETY_RULE = PROJECT_ROOT / "rules" / "decision-history-safety.md"

FIND_GITHUB_SPEC = importlib.util.spec_from_file_location("find_github_reference", FIND_GITHUB)
if FIND_GITHUB_SPEC is None or FIND_GITHUB_SPEC.loader is None:
    raise RuntimeError("could not load find_github_reference.py for tests")
FIND_GITHUB_MODULE = importlib.util.module_from_spec(FIND_GITHUB_SPEC)
sys.modules[FIND_GITHUB_SPEC.name] = FIND_GITHUB_MODULE
FIND_GITHUB_SPEC.loader.exec_module(FIND_GITHUB_MODULE)


def run_script(script: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, str(script), *args],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"{script.name} failed with {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def record(root: Path, timestamp: str, title: str, decision: str, *extra: str) -> subprocess.CompletedProcess[str]:
    return run_script(
        RECORD,
        "--root",
        str(root),
        "--timestamp",
        timestamp,
        "--title",
        title,
        "--decision",
        decision,
        "--scope",
        "testing",
        "--tag",
        "automation",
        *extra,
    )


def load_script(script: Path, name: str):
    module_spec = importlib.util.spec_from_file_location(name, script)
    if module_spec is None or module_spec.loader is None:
        raise RuntimeError(f"could not load {script.name}")
    module = importlib.util.module_from_spec(module_spec)
    with patch.object(sys, "path", [str(SCRIPTS), *sys.path]):
        module_spec.loader.exec_module(module)
    return module


class DecisionHistoryTests(unittest.TestCase):
    def test_agy_plugin_manifest_is_valid(self) -> None:
        manifest = json.loads(PLUGIN_MANIFEST.read_text(encoding="utf-8"))

        self.assertEqual(manifest["$schema"], "https://antigravity.google/schemas/v1/plugin.json")
        self.assertEqual(manifest["name"], "decision-history")
        self.assertTrue(manifest["description"])
        self.assertEqual(set(manifest), {"$schema", "name", "description"})

    def test_decision_history_safety_rule_forbids_destructive_changes(self) -> None:
        skill_text = SKILL.read_text(encoding="utf-8")
        safety_text = SAFETY_RULE.read_text(encoding="utf-8")

        self.assertIn("Never delete", skill_text)
        self.assertIn("MUST NOT delete", safety_text)
        self.assertIn("git rm", safety_text)
        self.assertIn("canonical source of truth", safety_text)
        self.assertIn("validation failures as reportable diagnostics", safety_text)
        self.assertIn("external URLs belong in `--source`", safety_text)
        self.assertIn("existing Markdown files that resolve inside `.knowledges/decisions/ref/`", safety_text)
        self.assertIn("### Writing a reference file", skill_text)
        self.assertIn("Create the reference file before recording the decision", skill_text)
        self.assertIn("path relative to `.knowledges/decisions/`", skill_text)
        self.assertIn("### Evidence discovery and persistence workflow", skill_text)
        self.assertIn("Without a repository", skill_text)
        self.assertIn("Without a reference", skill_text)
        self.assertIn("Do not pass `--decision-id` or `--output`", skill_text)
        self.assertIn("does not by itself authorize creating a reference file", skill_text)
        for text in (skill_text, safety_text):
            self.assertIn("append-only", text)
            self.assertIn("supersedes", text)

    def test_installed_skill_examples_use_the_installed_script_directory(self) -> None:
        skill_text = SKILL.read_text(encoding="utf-8")
        self.assertIn("SKILL_DIR", skill_text)
        self.assertIn('python3 "$SKILL_DIR/scripts/record_decision.py"', skill_text)
        self.assertIn('python3 "$SKILL_DIR/scripts/find_github_reference.py"', skill_text)
        self.assertNotIn("python3 skills/decision-history/scripts/", skill_text)

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            installed = base / "agent" / "decision-history"
            project = base / "project"
            shutil.copytree(SKILL.parent, installed)
            project.mkdir()
            created = run_script(
                installed / "scripts" / "record_decision.py", "--root", str(project),
                "--timestamp", "2026-10-28T10:00:00+07:00", "--title", "Installed",
                "--decision", "Use the installed helper.",
            )
            self.assertIn("prepended", created.stdout)
            verified = run_script(
                installed / "scripts" / "validate_decisions.py", "--root", str(project),
                "--require-integrity", "--require-history",
            )
            self.assertIn("validation passed", verified.stdout)

    def test_external_source_url_is_valid_but_external_ref_is_rejected_clearly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rejected = run_script(
                RECORD,
                "--root",
                str(root),
                "--timestamp",
                "2026-10-28T13:00:00+07:00",
                "--title",
                "Reject external reference input",
                "--decision",
                "Do not write an external URL as a local reference.",
                "--ref",
                "https://github.com/example/project",
                check=False,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("use --source or a local ref file", rejected.stderr)
            self.assertFalse((root / ".knowledges" / "decisions" / "2026-10.md").exists())

            record(
                root,
                "2026-10-28T14:00:00+07:00",
                "Use the external project source",
                "Use the external project source as context.",
                "--source",
                "https://github.com/example/project",
            )
            valid = run_script(VALIDATE, "--root", str(root), "--require-integrity")
            self.assertIn("validation passed", valid.stdout)

            period_file = root / ".knowledges" / "decisions" / "2026-10.md"
            content = period_file.read_text(encoding="utf-8").replace(
                "- References: none",
                "- References: `https://github.com/example/project`",
            )
            period_file.write_text(content, encoding="utf-8")
            invalid = run_script(VALIDATE, "--root", str(root), "--require-integrity", check=False)
            self.assertNotEqual(invalid.returncode, 0)
            self.assertIn("external reference", invalid.stderr)
            self.assertIn("record URLs in source or a local ref file", invalid.stderr)

    def test_reference_paths_must_stay_relative_and_confined(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root.parent / f"{root.name}-outside.md"
            outside.write_text("# outside\n", encoding="utf-8")
            common = [
                "--title",
                "Reject escaped reference",
                "--decision",
                "Do not accept a reference outside the project decision directory.",
            ]

            for reference in (str(outside), f"../{outside.name}"):
                rejected = run_script(
                    RECORD,
                    "--root",
                    str(root),
                    "--timestamp",
                    "2026-10-28T15:00:00+07:00",
                    *common,
                    "--ref",
                    reference,
                    check=False,
                )
                self.assertNotEqual(rejected.returncode, 0)
                if reference.startswith(".."):
                    self.assertIn("must stay inside", rejected.stderr)
                else:
                    self.assertIn("must be a relative path", rejected.stderr)

            reference_root = root / ".knowledges" / "decisions" / "ref"
            reference_root.mkdir(parents=True)
            escaped = reference_root / "escape.md"
            try:
                escaped.symlink_to(outside)
            except (NotImplementedError, OSError):
                self.skipTest("symbolic links are not available on this platform")

            rejected_symlink = run_script(
                RECORD,
                "--root",
                str(root),
                "--timestamp",
                "2026-10-28T15:01:00+07:00",
                *common,
                "--ref",
                "ref/escape.md",
                check=False,
            )
            self.assertNotEqual(rejected_symlink.returncode, 0)
            self.assertIn("must stay inside", rejected_symlink.stderr)
            self.assertFalse((root / ".knowledges" / "decisions" / "2026-10.md").exists())

    def test_decision_root_symlink_cannot_write_outside_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            external = Path(directory) / "external"
            external.mkdir()
            (root / ".knowledges").mkdir(parents=True)
            try:
                (root / ".knowledges" / "decisions").symlink_to(external, target_is_directory=True)
            except (NotImplementedError, OSError):
                self.skipTest("symbolic links are not available on this platform")

            rejected = run_script(
                RECORD, "--root", str(root), "--timestamp", "2026-10-28T10:00:00+07:00",
                "--title", "Escaped root", "--decision", "Keep writes inside the project.",
                check=False,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("decision directory must stay inside the project root", rejected.stderr)
            with self.assertRaisesRegex(ValueError, "decision directory must stay inside the project root"):
                FIND_GITHUB_MODULE.reference_output_path(root, "ref/evidence.md", None)
            self.assertEqual(list(external.iterdir()), [])

    def test_validator_rejects_external_decision_file_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            external = base / "external"
            project = base / "project"
            external.mkdir()
            project.mkdir()
            record(external, "2026-10-28T10:00:00+07:00", "Outside", "Use outside context.")
            decisions = project / ".knowledges" / "decisions"
            decisions.mkdir(parents=True)
            try:
                (decisions / "2026-10.md").symlink_to(
                    external / ".knowledges" / "decisions" / "2026-10.md"
                )
            except (NotImplementedError, OSError):
                self.skipTest("symbolic links are not available on this platform")

            for script in (VALIDATE, LIST, MIGRATE):
                result = run_script(script, "--root", str(project), check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("decision file must stay inside", result.stderr)

            (decisions / "2026-10.md").unlink()
            decisions.rmdir()
            decisions.symlink_to(external / ".knowledges" / "decisions", target_is_directory=True)
            rejected_root = run_script(VALIDATE, "--root", str(project), check=False)
            self.assertNotEqual(rejected_root.returncode, 0)
            self.assertIn("decision directory must stay inside", rejected_root.stderr)

    def test_github_token_is_removed_on_cross_origin_redirect(self) -> None:
        handler = FIND_GITHUB_MODULE.OriginBoundAuthorizationRedirect()
        request = Request("https://raw.githubusercontent.com/owner/repo/main/README.md")
        request.add_header("Authorization", "Bearer private-token")
        headers = Message()

        same_origin = handler.redirect_request(
            request, None, 302, "Found", headers,
            "https://raw.githubusercontent.com/owner/repo/main/OTHER.md",
        )
        self.assertIsNotNone(same_origin)
        self.assertEqual(same_origin.get_header("Authorization"), "Bearer private-token")

        other_origin = handler.redirect_request(
            request, None, 302, "Found", headers, "https://example.com/README.md",
        )
        self.assertIsNotNone(other_origin)
        self.assertFalse(other_origin.has_header("Authorization"))
        downgraded = handler.redirect_request(
            request, None, 302, "Found", headers,
            "http://raw.githubusercontent.com/owner/repo/main/README.md",
        )
        self.assertIsNotNone(downgraded)
        self.assertFalse(downgraded.has_header("Authorization"))

        with patch.dict("os.environ", {"GITHUB_TOKEN": "private-token"}), patch.object(
            FIND_GITHUB_MODULE, "build_opener"
        ) as build_opener:
            response = build_opener.return_value.open.return_value.__enter__.return_value
            response.read.return_value = b"# Example\n"
            self.assertEqual(
                FIND_GITHUB_MODULE.fetch_markdown(request.full_url, timeout=1), "# Example\n"
            )
            self.assertIsInstance(build_opener.call_args.args[0],
                                  FIND_GITHUB_MODULE.OriginBoundAuthorizationRedirect)

    def test_reference_integrity_detects_edits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / ".knowledges" / "decisions" / "ref" / "evidence.md"
            reference.parent.mkdir(parents=True)
            reference.write_text("# Original evidence\n", encoding="utf-8")

            record(
                root,
                "2026-10-28T15:02:00+07:00",
                "Protect reference integrity",
                "Keep the reference content unchanged after recording.",
                "--ref",
                "ref/evidence.md",
            )
            valid = run_script(VALIDATE, "--root", str(root), "--require-integrity")
            self.assertIn("validation passed", valid.stdout)

            reference.write_text("# Changed evidence\n", encoding="utf-8")
            invalid = run_script(VALIDATE, "--root", str(root), "--require-integrity", check=False)
            self.assertNotEqual(invalid.returncode, 0)
            self.assertIn("reference SHA256 does not match", invalid.stderr)

    def test_github_reference_extracts_sections_and_exclusions_without_network(self) -> None:
        markdown = """# Worker guide

## 22. Writing Modular Tasks in AppEventsWorkers

The main guidance.

### Benchmarks

Benchmark details that should be excluded.

### Implementation notes

Implementation details that should remain.

## Other section

Unrelated content.
"""
        extracted = FIND_GITHUB_MODULE.extract_sections(
            markdown,
            ["22-writing-modular-tasks-in-appeventsworkers"],
            ["benchmarks"],
        )
        self.assertIn("The main guidance.", extracted)
        self.assertIn("Implementation details that should remain.", extracted)
        self.assertNotIn("Benchmark details that should be excluded.", extracted)
        snapshot = FIND_GITHUB_MODULE.build_snapshot(
            "rachmanzz/gosuite",
            "main",
            "v3/worker/README.md",
            ["22-writing-modular-tasks-in-appeventsworkers"],
            ["benchmarks"],
            extracted,
            "DEC-20261028-200000",
        )
        self.assertIn("https://raw.githubusercontent.com/rachmanzz/gosuite/main/v3/worker/README.md", snapshot)
        self.assertIn("- Type: `GitHub Markdown section`", snapshot)
        self.assertIn("- URL: https://github.com/rachmanzz/gosuite/blob/main/v3/worker/README.md", snapshot)
        self.assertIn("## Context", snapshot)
        self.assertIn("## Evidence", snapshot)
        self.assertIn("Content SHA256", snapshot)
        self.assertNotIn("## Extracted content", snapshot)

    def test_github_exclusion_is_scoped_and_heading_slugs_are_unique(self) -> None:
        markdown = """## Alpha

### Benchmarks

Alpha results.

## Beta

Beta guidance.

### Benchmarks

Beta results.
"""
        extracted = FIND_GITHUB_MODULE.extract_sections(markdown, ["beta"], ["benchmarks"])
        self.assertIn("Beta guidance.", extracted)
        self.assertNotIn("Beta results.", extracted)
        self.assertNotIn("Alpha results.", extracted)

        collision = FIND_GITHUB_MODULE.parse_headings("# Benchmarks-1\n# Benchmarks\n# Benchmarks\n")
        self.assertEqual([heading.slug for heading in collision], ["benchmarks-1", "benchmarks", "benchmarks-2"])

    def test_github_reference_discovery_modes_require_markdown_and_list_headings(self) -> None:
        with self.assertRaises(ValueError):
            FIND_GITHUB_MODULE.normalize_path("docs/README.html")
        self.assertEqual(FIND_GITHUB_MODULE.normalize_path("docs/README.MD"), "docs/README.MD")

        markdown = """# Worker guide

## 22. Writing Modular Tasks in AppEventsWorkers

Content.
"""
        listing = FIND_GITHUB_MODULE.heading_listing(markdown)
        self.assertIn("`worker-guide`", listing)
        self.assertIn("`22-writing-modular-tasks-in-appeventsworkers`", listing)
        self.assertNotIn("Content.", listing)

        parser = FIND_GITHUB_MODULE.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["--repo", "owner/project", "--path", "README.md"])

        with patch.object(FIND_GITHUB_MODULE, "fetch_markdown", return_value=markdown) as fetch:
            listing_output = io.StringIO()
            with patch.object(
                sys,
                "argv",
                [
                    "find_github_reference.py",
                    "--repo",
                    "owner/project",
                    "--path",
                    "README.md",
                    "--list-headings",
                ],
            ), redirect_stdout(listing_output):
                self.assertEqual(FIND_GITHUB_MODULE.main(), 0)
            self.assertIn("`22-writing-modular-tasks-in-appeventsworkers`", listing_output.getvalue())

            output = io.StringIO()
            with patch.object(
                sys,
                "argv",
                [
                    "find_github_reference.py",
                    "--repo",
                    "owner/project",
                    "--path",
                    "README.md",
                    "--all",
                ],
            ), redirect_stdout(output):
                self.assertEqual(FIND_GITHUB_MODULE.main(), 0)
            self.assertEqual(output.getvalue(), markdown)
            self.assertEqual(fetch.call_count, 2)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                FIND_GITHUB_MODULE.reference_output_path(root, "ref/evidence.txt", None)
            with self.assertRaises(ValueError):
                FIND_GITHUB_MODULE.reference_output_path(root, str(root / "ref" / "evidence.md"), None)

            outside_ref_directory = root.parent / f"{root.name}-ref"
            outside_ref_directory.mkdir()
            reference_directory = root / ".knowledges" / "decisions" / "ref"
            reference_directory.parent.mkdir(parents=True)
            try:
                reference_directory.symlink_to(outside_ref_directory, target_is_directory=True)
            except (NotImplementedError, OSError):
                self.skipTest("symbolic links are not available on this platform")
            with self.assertRaises(ValueError):
                FIND_GITHUB_MODULE.reference_output_path(root, "ref/evidence.md", None)

        with patch.object(FIND_GITHUB_MODULE, "fetch_markdown", return_value=markdown) as fetch:
            output = io.StringIO()
            with patch.object(
                sys,
                "argv",
                [
                    "find_github_reference.py",
                    "--repo",
                    "owner/project",
                    "--path",
                    "README.md",
                    "--all",
                ],
            ), redirect_stdout(output):
                self.assertEqual(FIND_GITHUB_MODULE.main(), 0)
            self.assertEqual(output.getvalue(), markdown)
            fetch.assert_called_once()

        with patch.object(FIND_GITHUB_MODULE, "fetch_markdown") as fetch:
            errors = io.StringIO()
            with patch.object(
                sys,
                "argv",
                [
                    "find_github_reference.py",
                    "--repo",
                    "owner/project",
                    "--path",
                    "README.txt",
                    "--all",
                ],
            ), redirect_stderr(errors):
                self.assertEqual(FIND_GITHUB_MODULE.main(), 1)
            self.assertIn("ending in .md", errors.getvalue())
            fetch.assert_not_called()

    def test_setext_headings_are_listed_and_extractable(self) -> None:
        markdown = """Overview
========

Details
-------

Useful context.

## Details

Other context.
"""
        listing = FIND_GITHUB_MODULE.heading_listing(markdown)
        self.assertIn("`overview`", listing)
        self.assertIn("`details`", listing)
        self.assertIn("`details-1`", listing)
        extracted = FIND_GITHUB_MODULE.extract_sections(markdown, ["details"], [])
        self.assertIn("Useful context.", extracted)
        self.assertNotIn("Other context.", extracted)
        multiline = "A long\nheading\n=======\n\nBody.\n"
        self.assertIn("`a-long-heading`", FIND_GITHUB_MODULE.heading_listing(multiline))
        self.assertIn("Body.", FIND_GITHUB_MODULE.extract_sections(multiline, ["a-long-heading"], []))

    def test_indented_atx_heading_is_listed_and_extractable(self) -> None:
        markdown = "  ## Indented heading\n\nUseful guidance.\n\n## Other\n\nOutside.\n"
        listing = FIND_GITHUB_MODULE.heading_listing(markdown)
        self.assertIn("`indented-heading`", listing)
        extracted = FIND_GITHUB_MODULE.extract_sections(markdown, ["indented-heading"], [])
        self.assertIn("Useful guidance.", extracted)
        self.assertNotIn("Outside.", extracted)
        self.assertEqual(FIND_GITHUB_MODULE.parse_headings("    ## Code, not a heading\n"), [])

    def test_github_reference_snapshot_creation_is_exclusive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".knowledges" / "decisions" / "ref" / "snapshot.md"
            FIND_GITHUB_MODULE.create_reference_snapshot(path, "first snapshot\n")

            with self.assertRaises(FileExistsError):
                FIND_GITHUB_MODULE.create_reference_snapshot(path, "second snapshot\n")

            self.assertEqual(path.read_text(encoding="utf-8"), "first snapshot\n")

    def test_github_snapshot_with_internal_decision_symlink_returns_usable_ref(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            actual = root / "actual-decisions"
            actual.mkdir()
            (root / ".knowledges").mkdir()
            try:
                (root / ".knowledges" / "decisions").symlink_to(actual, target_is_directory=True)
            except (NotImplementedError, OSError):
                self.skipTest("symbolic links are not available on this platform")
            output = io.StringIO()
            with patch.object(FIND_GITHUB_MODULE, "fetch_markdown", return_value="# Intro\n\nContext.\n"), patch.object(
                sys, "argv", [
                    "find_github_reference.py", "--root", str(root), "--repo", "owner/project",
                    "--path", "README.md", "--id", "intro", "--decision-id", "DEC-20261028-100000",
                ],
            ), redirect_stdout(output):
                self.assertEqual(FIND_GITHUB_MODULE.main(), 0)
            self.assertTrue((actual / "ref" / "DEC-20261028-100000-github.md").is_file())
            self.assertIn("--ref ref/DEC-20261028-100000-github.md", output.getvalue())

    def test_companion_tool_policy_is_explicit(self) -> None:
        skill_text = SKILL.read_text(encoding="utf-8")
        companion_text = COMPANION_TOOLS.read_text(encoding="utf-8")

        self.assertLess(skill_text.index("decisiongraph/dg"), skill_text.index("Semantica"))
        self.assertIn("never installed automatically", skill_text)
        self.assertIn("explicit user request", skill_text)
        self.assertIn("canonical source of truth", skill_text)
        self.assertIn("AGPL-3.0-or-later", companion_text)
        self.assertIn("Apache-2.0", companion_text)
        self.assertIn("scope `companion-tool`", companion_text)

    def test_recording_prepends_and_validates_without_references(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record(root, "2026-10-28T10:00:00+07:00", "First decision", "Choose the first option.")
            period_file = root / ".knowledges" / "decisions" / "2026-10.md"
            first_block = period_file.read_text(encoding="utf-8")

            record(root, "2026-10-28T11:00:00+07:00", "Second decision", "Choose the second option.")
            content = period_file.read_text(encoding="utf-8")

            self.assertLess(content.index("## Second decision"), content.index("## First decision"))
            self.assertTrue(content.endswith(first_block))
            self.assertIn("- References: none", content)
            self.assertFalse((root / ".knowledges" / "decisions" / "ref").exists())
            validation = run_script(VALIDATE, "--root", str(root), "--require-integrity")
            self.assertIn("validation passed", validation.stdout)

    def test_new_markdown_ids_and_period_names_match_decided_at(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rejected_id = run_script(
                RECORD, "--root", str(root), "--timestamp", "2026-10-28T10:00:00+07:00",
                "--id", "DEC-20000101-000000", "--title", "Wrong ID", "--decision", "Use A.",
                check=False,
            )
            self.assertNotEqual(rejected_id.returncode, 0)
            self.assertIn("id must match the timestamp", rejected_id.stderr)

            record(root, "2026-10-28T10:00:00+07:00", "Right ID", "Use A.")
            period = root / ".knowledges" / "decisions" / "2026-10.md"
            period.rename(period.with_name("2026-09.md"))
            mismatch = run_script(VALIDATE, "--root", str(root), "--require-integrity", check=False)
            self.assertNotEqual(mismatch.returncode, 0)
            self.assertIn("period filename does not match decided_at", mismatch.stderr)

            arbitrary = period.with_name("notes.md")
            period.with_name("2026-09.md").rename(arbitrary)
            wrong_name = run_script(VALIDATE, "--root", str(root), "--require-integrity", check=False)
            self.assertIn("period filename does not match decided_at", wrong_name.stderr)

            period = arbitrary
            text = period.read_text(encoding="utf-8")
            period.write_text(text.replace("DEC-20261028-100000", "DEC-20000101-000000"), encoding="utf-8")
            wrong_id = run_script(VALIDATE, "--root", str(root), "--require-integrity", check=False)
            self.assertIn("decision ID does not match decided_at", wrong_id.stderr)

    def test_backdated_supersession_is_rejected_by_recorder_and_validator(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            later_id = "DEC-20261029-100000"
            record(
                root, "2026-10-29T10:00:00+07:00", "Later", "Use the later choice.",
                "--period", "date", "--id", later_id,
            )
            rejected = run_script(
                RECORD, "--root", str(root), "--timestamp", "2026-10-28T10:00:00+07:00",
                "--title", "Earlier", "--decision", "Use the earlier choice.",
                "--supersedes", later_id, check=False,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("cannot supersede later decision", rejected.stderr)
            month_file = root / ".knowledges" / "decisions" / "2026-10.md"
            self.assertFalse(month_file.exists())

            record(root, "2026-10-28T10:00:00+07:00", "Earlier", "Use the earlier choice.")
            month_file.write_text(
                month_file.read_text(encoding="utf-8").replace(
                    "- Supersedes: none", f"- Supersedes: `{later_id}`",
                ),
                encoding="utf-8",
            )
            result = run_script(VALIDATE, "--root", str(root), "--require-integrity", check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("cannot supersede later decision", result.stderr)
            listing = run_script(LIST, "--root", str(root), check=False)
            self.assertNotEqual(listing.returncode, 0)
            self.assertIn("cannot supersede later decision", listing.stderr)

    def test_mixed_timezone_timestamps_report_error_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record(root, "2026-10-28T10:00:00+07:00", "First", "Use option A.")
            record(root, "2026-10-28T11:00:00+07:00", "Second", "Use option B.")
            period_file = root / ".knowledges" / "decisions" / "2026-10.md"
            period_file.write_text(
                period_file.read_text(encoding="utf-8").replace(
                    "2026-10-28T11:00:00+07:00", "2026-10-28T11:00:00", 1,
                ),
                encoding="utf-8",
            )
            result = run_script(VALIDATE, "--root", str(root), "--require-integrity", check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("decided_at must include a timezone", result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_three_digit_collision_suffix_is_valid_everywhere(self) -> None:
        module_spec = importlib.util.spec_from_file_location("record_decision_for_test", RECORD)
        if module_spec is None or module_spec.loader is None:
            self.fail("could not load record_decision.py for tests")
        with patch.object(sys, "path", [str(SCRIPTS), *sys.path]):
            recorder = importlib.util.module_from_spec(module_spec)
            module_spec.loader.exec_module(recorder)
        timestamp = recorder.parse_timestamp("2026-10-28T10:00:00+07:00")
        base = "DEC-20261028-100000"
        used = {base, *(f"{base}-{number:02d}" for number in range(2, 100))}
        self.assertEqual(recorder.choose_id(timestamp, used, None), f"{base}-100")
        self.assertTrue(FIND_GITHUB_MODULE.DECISION_ID_PATTERN.fullmatch(f"{base}-100"))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record(root, "2026-10-28T10:00:00+07:00", "Hundredth", "Use option 100.", "--id", f"{base}-100")
            result = run_script(VALIDATE, "--root", str(root), "--require-integrity")
            self.assertIn("validation passed", result.stdout)

    def test_search_hides_superseded_decisions_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record(
                root,
                "2026-10-28T10:00:00+07:00",
                "Use the old queue",
                "Use the old queue for background work.",
                "--id",
                "DEC-20261028-100000",
            )
            record(
                root,
                "2026-10-28T11:00:00+07:00",
                "Use the new queue",
                "Use the new queue for background work.",
                "--id",
                "DEC-20261028-110000",
                "--supersedes",
                "DEC-20261028-100000",
            )
            record(
                root,
                "2026-10-28T12:00:00+07:00",
                "Use a cache",
                "Use a cache for read-heavy work.",
                "--id",
                "DEC-20261028-120000",
            )

            active = run_script(LIST, "--root", str(root), "--scope", "testing")
            self.assertIn("DEC-20261028-110000 [active]", active.stdout)
            self.assertIn("DEC-20261028-120000 [active]", active.stdout)
            self.assertNotIn("DEC-20261028-100000", active.stdout)

            all_records = run_script(LIST, "--root", str(root), "--include-superseded", "--json")
            payload = json.loads(all_records.stdout)
            statuses = {item["id"]: item["status"] for item in payload}
            self.assertEqual(statuses["DEC-20261028-100000"], "superseded")
            self.assertEqual(statuses["DEC-20261028-110000"], "active")

    def test_migration_is_report_only_and_draft_is_outside_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            decision_root = root / ".knowledges" / "decisions"
            decision_root.mkdir(parents=True)
            (decision_root / "2026-10.md").write_text(
                """<!-- decision:start -->
## Legacy YAML decision

```yaml
id: "DEC-20261028-100000"
title: "Legacy YAML decision"
decided_at: "2026-10-28T10:00:00+07:00"
scope: "legacy"
tags: ["old"]
supersedes: []
related: []
refs: []
source: ["conversation"]
integrity_sha256: ""
```

### Decision

Keep the legacy decision unchanged.

<!-- decision:end -->
""",
                encoding="utf-8",
            )
            source_before = (decision_root / "2026-10.md").read_text(encoding="utf-8")
            draft = root / "migration-review.md"
            result = run_script(MIGRATE, "--root", str(root), "--draft", str(draft))

            self.assertIn("Found 1 legacy decision record", result.stdout)
            self.assertEqual(source_before, (decision_root / "2026-10.md").read_text(encoding="utf-8"))
            self.assertTrue(draft.is_file())
            draft_text = draft.read_text(encoding="utf-8")
            self.assertIn("- ID: `DEC-20261028-100000`", draft_text)
            self.assertNotIn("```yaml", draft_text)
            self.assertNotIn("migration-review.md", str(decision_root))

    def test_migration_draft_does_not_overwrite_concurrently_created_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            decision_root = root / ".knowledges" / "decisions"
            decision_root.mkdir(parents=True)
            (decision_root / "2026-10.md").write_text(
                '---\nid: "DEC-20261028-100000"\ntitle: "Old"\n'
                'decided_at: "2026-10-28T10:00:00+07:00"\nscope: "project"\n'
                '---\n\n## Decision\n\nUse A.\n', encoding="utf-8",
            )
            draft = root / "draft.md"
            original_exists = Path.exists
            injected = False

            def exists_after_concurrent_create(path: Path) -> bool:
                nonlocal injected
                if path == draft and not injected:
                    draft.write_text("Created by another writer.\n", encoding="utf-8")
                    injected = True
                    return False
                return original_exists(path)

            migrate_module = load_script(MIGRATE, "migrate_exclusive_test")
            with patch.object(Path, "exists", exists_after_concurrent_create), patch.object(
                sys, "argv", ["migrate_decisions.py", "--root", str(root), "--draft", str(draft)]
            ), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(migrate_module.main(), 1)
            self.assertIn("draft already exists", errors.getvalue())
            self.assertEqual(draft.read_text(encoding="utf-8"), "Created by another writer.\n")

    def test_tampering_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record(root, "2026-10-28T10:00:00+07:00", "Integrity decision", "Keep the recorded text.")
            period_file = root / ".knowledges" / "decisions" / "2026-10.md"
            period_file.write_text(
                period_file.read_text(encoding="utf-8").replace("Keep the recorded text.", "Changed later."),
                encoding="utf-8",
            )
            validation = run_script(VALIDATE, "--root", str(root), "--require-integrity", check=False)
            self.assertNotEqual(validation.returncode, 0)
            self.assertIn("integrity_sha256 does not match", validation.stderr)

            search = run_script(LIST, "--root", str(root), check=False)
            self.assertNotEqual(search.returncode, 0)
            self.assertIn("integrity_sha256 does not match", search.stderr)

    def test_listing_uses_the_validated_snapshot_without_rereading(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record(root, "2026-10-28T10:00:00+07:00", "Original", "Use original.")
            period = root / ".knowledges" / "decisions" / "2026-10.md"
            list_module = load_script(LIST, "list_snapshot_test")
            original_scan = list_module.scan_history

            def scan_then_change(*args: object, **kwargs: object):
                result = original_scan(*args, **kwargs)
                period.write_text(
                    period.read_text(encoding="utf-8").replace("Use original.", "Use modified."),
                    encoding="utf-8",
                )
                return result

            output = io.StringIO()
            with patch.object(list_module, "scan_history", scan_then_change), patch.object(
                sys, "argv", ["list_decisions.py", "--root", str(root), "--query", "modified"]
            ), redirect_stdout(output):
                self.assertEqual(list_module.main(), 0)
            self.assertEqual(output.getvalue().strip(), "No matching decisions.")
            invalid = run_script(VALIDATE, "--root", str(root), "--require-integrity", check=False)
            self.assertIn("integrity_sha256 does not match", invalid.stderr)

    def test_listing_rejects_history_with_removed_integrity_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record(root, "2026-10-28T10:00:00+07:00", "Original", "Use option A.")
            period_file = root / ".knowledges" / "decisions" / "2026-10.md"
            altered = period_file.read_text(encoding="utf-8").replace("Use option A.", "Use option B.")
            altered = re.sub(r"^- Integrity SHA256:.*$", "- Integrity SHA256: ``", altered, flags=re.MULTILINE)
            period_file.write_text(altered, encoding="utf-8")

            search = run_script(LIST, "--root", str(root), check=False)
            self.assertNotEqual(search.returncode, 0)
            self.assertIn("missing integrity_sha256", search.stderr)
            self.assertNotIn("[active]", search.stdout)

    def test_unverified_legacy_requires_explicit_inspection_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            decisions = root / ".knowledges" / "decisions"
            decisions.mkdir(parents=True)
            (decisions / "2026-10.md").write_text(
                """<!-- decision:start -->
## Legacy decision

```yaml
id: "DEC-20261028-100000"
title: "Legacy decision"
decided_at: "2026-10-28T10:00:00+07:00"
scope: "testing"
tags: []
supersedes: []
related: []
refs: []
source: []
integrity_sha256: ""
```

### Decision

Keep the historical choice.

<!-- decision:end -->
""", encoding="utf-8",
            )
            default = run_script(LIST, "--root", str(root), check=False)
            self.assertNotEqual(default.returncode, 0)
            self.assertIn("missing integrity_sha256", default.stderr)

            inspection = run_script(
                LIST, "--root", str(root), "--include-unverified-legacy", "--json"
            )
            self.assertEqual(json.loads(inspection.stdout)[0]["integrity"], "unverified")
            self.assertIn("no integrity_sha256", inspection.stderr)
            self.assertIn(
                "[unverified]", run_script(
                    LIST, "--root", str(root), "--include-unverified-legacy"
                ).stdout,
            )

            modern_root = root / "modern"
            record(modern_root, "2026-10-28T11:00:00+07:00", "Modern", "Use option B.")
            modern_path = modern_root / ".knowledges" / "decisions" / "2026-10.md"
            modern_path.write_text(
                re.sub(
                    r"^- Integrity SHA256:.*$", "- Integrity SHA256: ``",
                    modern_path.read_text(encoding="utf-8"), flags=re.MULTILINE,
                ), encoding="utf-8",
            )
            modern = run_script(
                LIST, "--root", str(modern_root), "--include-unverified-legacy", check=False
            )
            self.assertNotEqual(modern.returncode, 0)
            self.assertIn("missing integrity_sha256", modern.stderr)

    def test_required_history_rejects_empty_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = run_script(
                VALIDATE, "--root", directory, "--require-integrity", "--require-history", check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("no decision history files found", result.stderr)
            self.assertNotIn("Traceback", result.stderr)

            fresh_project = run_script(VALIDATE, "--root", directory, "--require-integrity")
            self.assertIn("validation passed", fresh_project.stdout)

        workflow = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("validate_decisions.py --root . --require-integrity --require-history", workflow)
        self.assertIn("fetch-depth: 0", workflow)
        self.assertEqual(workflow.count('--against-git-ref "$BASELINE_REF"'), 2)
        self.assertIn("github.event.pull_request.base.sha", workflow)
        self.assertIn("github.event.before", workflow)

    def test_git_baseline_rejects_removed_period_with_remaining_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.com"], check=True)
            record(root, "2026-09-28T10:00:00+07:00", "First", "Use option A.")
            record(root, "2026-10-28T10:00:00+07:00", "Second", "Use option B.")
            subprocess.run(["git", "-C", str(root), "add", ".knowledges/decisions"], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "fixture"], check=True)
            (root / ".knowledges" / "decisions" / "2026-09.md").unlink()

            insufficient = run_script(
                VALIDATE, "--root", str(root), "--require-integrity", "--require-history"
            )
            self.assertIn("validation passed", insufficient.stdout)
            protected = run_script(
                VALIDATE, "--root", str(root), "--require-integrity", "--require-history",
                "--against-git-ref", "HEAD", check=False,
            )
            self.assertNotEqual(protected.returncode, 0)
            self.assertIn("removed", protected.stderr)

    def test_malformed_legacy_link_list_reports_error_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            decision_root = root / ".knowledges" / "decisions"
            decision_root.mkdir(parents=True)
            (decision_root / "2026-10.md").write_text(
                """---
id: "DEC-20261028-100000"
title: "Malformed legacy link"
decided_at: "2026-10-28T10:00:00+07:00"
scope: "testing"
supersedes: [{}]
related: []
refs: []
source: ["conversation"]
---

## Decision

Use option A.
""",
                encoding="utf-8",
            )
            result = run_script(VALIDATE, "--root", str(root), check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("supersedes[0] must be a non-empty string", result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_recording_rejects_malformed_candidate_before_writing(self) -> None:
        cases = (
            ("Marker in title <!-- decision:end -->", "Use option A.", ()),
            ("Missing statement", "### Rationale\nOnly a rationale.", ()),
            ("Marker in source", "Use option A.", ("--source", "<!-- decision:end -->")),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, (title, decision, extra) in enumerate(cases):
                target = root / str(index)
                result = run_script(
                    RECORD,
                    "--root", str(target),
                    "--timestamp", "2026-10-28T10:00:00+07:00",
                    "--title", title,
                    "--decision", decision,
                    *extra,
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((target / ".knowledges" / "decisions" / "2026-10.md").exists())

    def test_fenced_yaml_in_decision_body_remains_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record(
                root,
                "2026-10-28T10:00:00+07:00",
                "Document YAML example",
                "Use this setting:\n\n```yaml\nsetting: on\n```",
            )
            run_script(VALIDATE, "--root", str(root), "--require-integrity")
            found = json.loads(run_script(LIST, "--root", str(root), "--json").stdout)
            self.assertEqual(found[0]["format"], "markdown")

    def test_validator_rejects_text_outside_decision_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record(root, "2026-10-28T10:00:00+07:00", "First", "Use option A.")
            path = root / ".knowledges" / "decisions" / "2026-10.md"
            original = path.read_text(encoding="utf-8")
            for altered in ("stray text\n" + original, original + "\nstray text\n"):
                path.write_text(altered, encoding="utf-8")
                result = run_script(VALIDATE, "--root", str(root), "--require-integrity", check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("text outside decision blocks", result.stderr)
            path.write_text(original, encoding="utf-8")
            run_script(VALIDATE, "--root", str(root), "--require-integrity")

    def test_id_mentioned_only_in_prose_cannot_be_superseded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record(root, "2026-10-28T10:00:00+07:00", "First", "Mention DEC-20261028-090000 only.")
            path = root / ".knowledges" / "decisions" / "2026-10.md"
            original = path.read_text(encoding="utf-8")
            result = run_script(
                RECORD, "--root", str(root), "--timestamp", "2026-10-28T11:00:00+07:00",
                "--title", "Second", "--decision", "Use option B.",
                "--supersedes", "DEC-20261028-090000", check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unknown decision", result.stderr)
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_metadata_lists_preserve_commas_inside_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record(
                root, "2026-10-28T10:00:00+07:00", "First", "Use option A.",
                "--tag", "one,two", "--source", "https://example.com/a,b",
            )
            run_script(VALIDATE, "--root", str(root), "--require-integrity")
            found = json.loads(run_script(LIST, "--root", str(root), "--json").stdout)
            self.assertEqual(found[0]["tags"], ["automation", "one,two"])
            self.assertEqual(found[0]["source"], ["https://example.com/a,b"])

    def test_correction_preserves_prior_validation_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record(root, "2026-10-28T10:00:00+07:00", "First", "Use option A.")
            path = root / ".knowledges" / "decisions" / "2026-10.md"
            changed = path.read_text(encoding="utf-8").replace("Use option A.", "Changed option.")
            path.write_text(changed, encoding="utf-8")
            record(
                root, "2026-10-28T11:00:00+07:00", "Correction", "Use option B.",
                "--supersedes", "DEC-20261028-100000",
            )
            result = run_script(VALIDATE, "--root", str(root), "--require-integrity", check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("integrity_sha256 does not match", result.stderr)

    def test_concurrent_period_files_use_distinct_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            timestamp = "2026-10-28T13:00:00+07:00"

            def write(index: int) -> subprocess.CompletedProcess[str]:
                return record(
                    root, timestamp, f"Concurrent cross-period {index}", f"Choose option {index}.",
                    "--period", "month" if index % 2 else "date",
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                results = list(executor.map(write, range(8)))
            self.assertEqual(len(results), 8)
            run_script(VALIDATE, "--root", str(root), "--require-integrity")
            found = json.loads(run_script(LIST, "--root", str(root), "--json").stdout)
            ids = [item["id"] for item in found]
            self.assertEqual(len(ids), len(set(ids)))

    def test_concurrent_writers_do_not_lose_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            timestamp = "2026-10-28T13:00:00+07:00"

            def write(index: int) -> subprocess.CompletedProcess[str]:
                return record(
                    root,
                    timestamp,
                    f"Concurrent decision {index}",
                    f"Choose concurrent option {index}.",
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                results = list(executor.map(write, range(8)))

            self.assertEqual(len(results), 8)
            period_file = root / ".knowledges" / "decisions" / "2026-10.md"
            content = period_file.read_text(encoding="utf-8")
            self.assertEqual(content.count("<!-- decision:start -->"), 8)
            ids = re.findall(r"- ID: `([^`]+)`", content)
            self.assertEqual(len(ids), 8)
            self.assertEqual(len(set(ids)), 8)
            run_script(VALIDATE, "--root", str(root), "--require-integrity")

    def test_decision_heading_must_be_real_level_three_section(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record(root, "2026-10-29T12:00:00+07:00", "Decision", "Use option A.")
            period = root / ".knowledges/decisions/2026-10.md"
            original = period.read_text(encoding="utf-8")
            for replacement in (
                "### Rationale\n\nNo decision section.",
                "```markdown\n### Decision\nFake section.\n```",
            ):
                altered = original.replace("### Decision\n\nUse option A.", replacement)
                altered = re.sub(r"^- Integrity SHA256:.*$", "- Integrity SHA256: ``", altered, flags=re.MULTILINE)
                digest = hashlib.sha256(altered.encode("utf-8")).hexdigest()
                period.write_text(altered.replace("- Integrity SHA256: ``", f"- Integrity SHA256: `{digest}`"))
                result = run_script(VALIDATE, "--root", str(root), "--require-integrity", check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Decision section", result.stderr)

    def test_invalid_collision_suffixes_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for suffix in ("00", "01", "001"):
                result = run_script(
                    RECORD, "--root", str(root), "--timestamp", "2026-10-29T12:00:00+07:00",
                    "--title", "Invalid suffix", "--decision", "Use option A.",
                    "--id", f"DEC-20261029-120000-{suffix}", check=False,
                )
                self.assertNotEqual(result.returncode, 0)

    def test_broken_decision_symlink_is_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            period = root / ".knowledges/decisions/2026-10.md"
            period.parent.mkdir(parents=True)
            period.symlink_to("missing.md")
            for script in (VALIDATE, LIST, MIGRATE):
                result = run_script(script, "--root", str(root), check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("Traceback", result.stderr)

    def test_github_heading_parser_ignores_html_comments(self) -> None:
        source = "<!--\n## Hidden\nNot evidence.\n-->\n\n## Visible\nActual evidence.\n"
        headings = FIND_GITHUB_MODULE.parse_headings(source)
        self.assertEqual([heading.slug for heading in headings], ["visible"])
        with self.assertRaises(ValueError):
            FIND_GITHUB_MODULE.extract_sections(source, ["hidden"], [])

    def test_baseline_rejects_mode_change_and_staged_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.com"], check=True)
            ref = root / ".knowledges/decisions/ref/evidence.md"
            ref.parent.mkdir(parents=True)
            ref.write_text("# Evidence\n", encoding="utf-8")
            duplicate = ref.parent / "duplicate.md"
            duplicate.write_text("# Evidence\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", ".knowledges/decisions"], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "baseline"], check=True)

            ref.unlink()
            ref.symlink_to("duplicate.md")
            changed_mode = run_script(VALIDATE, "--root", str(root), "--against-git-ref", "HEAD", check=False)
            self.assertNotEqual(changed_mode.returncode, 0)
            self.assertIn("mode", changed_mode.stderr)

            ref.unlink()
            ref.write_text("# Altered\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", ".knowledges/decisions"], check=True)
            ref.write_text("# Evidence\n", encoding="utf-8")
            staged = run_script(VALIDATE, "--root", str(root), "--against-git-ref", "HEAD", check=False)
            self.assertNotEqual(staged.returncode, 0)
            self.assertIn("staged", staged.stderr)

    def test_baseline_rejects_new_yaml_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.com"], check=True)
            (root / "README.md").write_text("fixture\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "README.md"], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "baseline"], check=True)
            period = root / ".knowledges/decisions/2026-10.md"
            period.parent.mkdir(parents=True)
            block = '''<!-- decision:start -->
## New YAML decision

```yaml
id: "DEC-20261029-120000"
title: "New YAML decision"
decided_at: "2026-10-29T12:00:00+07:00"
scope: "project"
tags: []
supersedes: []
related: []
refs: []
source: []
integrity_sha256: ""
```

### Decision

Use option A.

<!-- decision:end -->
'''
            digest = hashlib.sha256(block.encode("utf-8")).hexdigest()
            period.write_text(block.replace('integrity_sha256: ""', f'integrity_sha256: "{digest}"'))
            result = run_script(
                VALIDATE, "--root", str(root), "--require-integrity", "--against-git-ref", "HEAD", check=False
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("new history must use Markdown", result.stderr)

    def test_backticked_none_and_hash_in_title_or_body_validate_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record(
                root,
                "2026-10-28T10:00:00+07:00",
                "Adopt C#",
                'Keep C# service config and ignore literal\nintegrity_sha256: "example"\nin body text.',
            )
            period = root / ".knowledges" / "decisions" / "2026-10.md"
            valid = run_script(VALIDATE, "--root", str(root), "--require-integrity")
            self.assertIn("validation passed", valid.stdout)

            content = period.read_text(encoding="utf-8")
            content = content.replace("- Supersedes: none", "- Supersedes: `none`")
            content = content.replace("- Related: none", "- Related: `none`")
            content = content.replace("- References: none", "- References: `none`")
            content = content.replace("- Reference SHA256: none", "- Reference SHA256: `none`")
            content = re.sub(r"^- Integrity SHA256:.*$", "- Integrity SHA256: ``", content, flags=re.MULTILINE)
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            period.write_text(
                content.replace("- Integrity SHA256: ``", f"- Integrity SHA256: `{digest}`"),
                encoding="utf-8",
            )
            valid_backtick_none = run_script(VALIDATE, "--root", str(root), "--require-integrity")
            self.assertIn("validation passed", valid_backtick_none.stdout)

    def test_unknown_markdown_metadata_line_and_flag_baseline_ref_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record(root, "2026-10-28T10:00:00+07:00", "Valid", "Use option A.")
            flag_ref = run_script(VALIDATE, "--root", str(root), "--against-git-ref=--full-tree", check=False)
            self.assertNotEqual(flag_ref.returncode, 0)
            self.assertIn("invalid Git baseline reference", flag_ref.stderr)

            period = root / ".knowledges" / "decisions" / "2026-10.md"
            altered = period.read_text(encoding="utf-8").replace(
                "- Scope: `testing`\n",
                "- Scope: `testing`\n- Status: `superseded`\n",
            )
            period.write_text(altered, encoding="utf-8")
            invalid = run_script(VALIDATE, "--root", str(root), check=False)
            self.assertNotEqual(invalid.returncode, 0)
            self.assertIn("invalid metadata line", invalid.stderr)

    def test_github_git_ref_rejects_traversal_and_backticks(self) -> None:
        for bad_ref in ("", "../other", "a/../../b", "-flag", "main`injected`", "main\ninjected"):
            with self.assertRaises(ValueError):
                FIND_GITHUB_MODULE.normalize_git_ref(bad_ref)
        self.assertEqual(FIND_GITHUB_MODULE.normalize_git_ref("feature/v1.0"), "feature/v1.0")


if __name__ == "__main__":
    unittest.main()
