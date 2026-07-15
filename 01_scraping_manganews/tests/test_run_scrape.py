from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_scrape.py"
SPEC = importlib.util.spec_from_file_location("run_scrape", SCRIPT_PATH)
run_scrape = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(run_scrape)


class RunScrapeTests(unittest.TestCase):
    def test_validate_and_deduplicate_keeps_latest_url(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.jsonl"
            destination = Path(directory) / "destination.jsonl"
            source.write_text(
                "\n".join(
                    [
                        json.dumps({"url": "https://example/a", "version": 1}),
                        json.dumps({"url": "https://example/b", "version": 1}),
                        json.dumps({"url": "https://example/a", "version": 2}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            count, duplicates = run_scrape.validate_and_deduplicate_jsonl(
                source, destination, "url"
            )

            rows = [
                json.loads(line)
                for line in destination.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(count, 2)
            self.assertEqual(duplicates, 1)
            self.assertEqual(rows[0]["version"], 2)

    def test_default_threshold_refuses_large_regression(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "series.jsonl"
            target.write_text("{}\n" * 11_415, encoding="utf-8")
            args = Namespace(smoke=False, min_items=None)

            required = run_scrape.required_item_count(
                args, run_scrape.DATASETS["series"], target
            )

            self.assertEqual(required, 10_844)

    def test_explicit_threshold_takes_precedence(self):
        args = Namespace(smoke=False, min_items=12_000)

        required = run_scrape.required_item_count(
            args, run_scrape.DATASETS["series"], Path("absent.jsonl")
        )

        self.assertEqual(required, 12_000)

    def test_resume_uses_a_fresh_job_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            (run_dir / "job-resume-001").mkdir()

            job_dir = run_scrape.resolve_job_dir(run_dir, resumed=True)

            self.assertEqual(job_dir, run_dir / "job-resume-002")


if __name__ == "__main__":
    unittest.main()
