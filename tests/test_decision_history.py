from __future__ import annotations

import concurrent.futures
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "skills" / "decision-history" / "scripts"
RECORD = SCRIPTS / "record_decision.py"
VALIDATE = SCRIPTS / "validate_decisions.py"
LIST = SCRIPTS / "list_decisions.py"
MIGRATE = SCRIPTS / "migrate_decisions.py"
SKILL = PROJECT_ROOT / "skills" / "decision-history" / "SKILL.md"
COMPANION_TOOLS = PROJECT_ROOT / "skills" / "decision-history" / "references" / "companion-tools.md"
PLUGIN_MANIFEST = PROJECT_ROOT / "plugin.json"


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


class DecisionHistoryTests(unittest.TestCase):
    def test_agy_plugin_manifest_is_valid(self) -> None:
        manifest = json.loads(PLUGIN_MANIFEST.read_text(encoding="utf-8"))

        self.assertEqual(manifest["$schema"], "https://antigravity.google/schemas/v1/plugin.json")
        self.assertEqual(manifest["name"], "decision-history")
        self.assertTrue(manifest["description"])
        self.assertEqual(set(manifest), {"$schema", "name", "description"})

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


if __name__ == "__main__":
    unittest.main()
