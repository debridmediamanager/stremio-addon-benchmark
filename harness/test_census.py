#!/usr/bin/env python3
import contextlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock
import urllib.error
import urllib.parse

import census


@unittest.skipUnless(shutil.which("7z"), "7z is required for census archives")
class CensusTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.out = self.root / "availability.json"
        self.archive = self.root / "availability.7z"
        paths = {
            "CONFIG": self.root / "indexers.json",
            "CANDIDATES": self.root / "candidates.json",
            "RAW_DIR": self.root / "raw",
            "OUT": self.out,
            "ARCHIVE": self.archive,
        }
        patcher = mock.patch.multiple(census, **{key: str(path) for key, path in paths.items()})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(setattr, census, "USER_AGENT", census.USER_AGENT)
        self.indexers = [
            {"label": label, "kind": "public-paid", "url": "https://indexer.invalid",
             "api_key": f"private-{label}"}
            for label in ("indexer-a", "indexer-b")
        ]
        self.write("indexers.json", {"indexers": self.indexers})
        self.write("candidates.json", {"candidates": [
            {"id": "tt0000001", "type": "movie"},
            {"id": "tt0000002", "type": "movie"},
        ]})
        self.previous = {
            "measured_utc": "2026-09-07T00:00:00Z",
            "indexers": [{"label": i["label"], "kind": i["kind"], "api_path": "/api"}
                         for i in self.indexers],
            "results": {
                "tt0000001": {"indexer-a": self.row(2), "indexer-b": self.row(3)},
                "tt0000002": {"indexer-a": self.row(4), "indexer-b": self.row(5)},
            },
        }
        self.write("availability.json", self.previous)

    def write(self, filename, document):
        (self.root / filename).write_text(json.dumps(document))

    @staticmethod
    def row(total):
        return {"total": total, "error": None, "kept": [], "sizes": [], "elapsed_s": 0}

    def run_census(self, *args, body=None, failure=None):
        calls = []

        def fetch(url, on_wait=None):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            calls.append(("tt" + query["imdbid"][0], query["apikey"][0]))
            if failure:
                raise failure
            return 200, body or '{"channel":{"item":[]}}', 0.01

        with mock.patch("sys.argv", ["census.py", "--pace", "0", *args]), \
                mock.patch.object(census, "fetch", side_effect=fetch), \
                contextlib.redirect_stdout(io.StringIO()):
            census.main()
        return json.loads(self.out.read_text()), calls

    def test_narrowed_run_preserves_unqueried_rows_and_archive(self):
        document, calls = self.run_census("--indexer", "indexer-a", "--limit", "1")
        self.assertEqual(calls, [("tt0000001", "private-indexer-a")])
        self.assertEqual(document["results"]["tt0000001"]["indexer-a"]["total"], 0)
        self.assertEqual(document["results"]["tt0000001"]["indexer-b"],
                         self.previous["results"]["tt0000001"]["indexer-b"])
        self.assertEqual(document["results"]["tt0000002"], self.previous["results"]["tt0000002"])
        self.out.unlink()
        with contextlib.redirect_stdout(io.StringIO()):
            restored = census.load_census()
        self.assertEqual(restored, document)

    def test_narrowed_run_restores_previous_archive_on_fresh_clone(self):
        census.archive()
        self.out.unlink()
        document, _ = self.run_census("--indexer", "indexer-a", "--limit", "1")
        self.assertEqual(document["results"]["tt0000002"], self.previous["results"]["tt0000002"])
        self.assertEqual(document["results"]["tt0000001"]["indexer-b"],
                         self.previous["results"]["tt0000001"]["indexer-b"])

    def test_retry_queries_failed_and_missing_pairs_not_successes(self):
        self.previous["results"]["tt0000001"]["indexer-b"]["error"] = "http 429"
        del self.previous["results"]["tt0000002"]["indexer-b"]
        self.write("availability.json", self.previous)
        document, calls = self.run_census("--retry-failed")
        self.assertEqual(calls, [("tt0000001", "private-indexer-b"),
                                 ("tt0000002", "private-indexer-b")])
        for title in ("tt0000001", "tt0000002"):
            self.assertEqual(document["results"][title]["indexer-a"],
                             self.previous["results"][title]["indexer-a"])
            self.assertIsNone(document["results"][title]["indexer-b"]["error"])
            self.assertEqual(document["results"][title]["indexer-b"]["total"], 0)

    def test_sizes_include_release_after_kept_names_in_both_encodings(self):
        sizes = [9 * 1024**3] * census.KEEP + [1024**3, 0, -1, None]
        json_body = json.dumps({"channel": {"response": {"total": 100}, "item": [
            {"title": f"Matrix.release.{number}", "size": size}
            for number, size in enumerate(sizes)
        ]}})
        xml_body = '<rss xmlns:newznab="http://www.newznab.com/DTD/2010/feeds/attributes/"><channel>'
        xml_body += '<newznab:response total="100"/>'
        xml_body += "".join(f'<item><title>Matrix.release.{number}</title><newznab:attr name="size" value="{size}"/></item>'
                            for number, size in enumerate(sizes))
        xml_body += "</channel></rss>"
        for body in (json_body, xml_body):
            with self.subTest(encoding=body[0]):
                document, _ = self.run_census("--indexer", "indexer-a", "--limit", "1", body=body)
                row = document["results"]["tt0000001"]["indexer-a"]
                self.assertEqual(row["total"], 100)
                self.assertEqual([item["size"] for item in row["kept"]], [9 * 1024**3] * census.KEEP)
                self.assertEqual(row["sizes"], [9 * 1024**3] * census.KEEP + [1024**3])

    def test_quota_drop_preserves_indexer_metadata_and_unqueried_rows(self):
        failure = urllib.error.HTTPError("https://indexer.invalid", 429, "quota", {}, None)
        document, calls = self.run_census("--indexer", "indexer-a", "--give-up-after", "1", failure=failure)
        self.assertEqual(calls, [("tt0000001", "private-indexer-a")])
        self.assertEqual(document["indexers"], self.previous["indexers"])
        self.assertIn("429", document["results"]["tt0000001"]["indexer-a"]["error"])
        self.assertEqual(document["results"]["tt0000002"], self.previous["results"]["tt0000002"])

    def test_merged_rows_are_checked_against_unselected_keys(self):
        self.previous["results"]["tt0000002"]["indexer-b"]["error"] = "private-indexer-b"
        self.write("availability.json", self.previous)
        with self.assertRaisesRegex(SystemExit, "api key survived"):
            self.run_census("--indexer", "indexer-a", "--limit", "1")
        self.assertEqual(json.loads(self.out.read_text()), self.previous)


if __name__ == "__main__":
    unittest.main()
