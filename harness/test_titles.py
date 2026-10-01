#!/usr/bin/env python3
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import census
import titles


class ParityIndexerTests(unittest.TestCase):
    def test_current_measurement_selects_the_parity_set(self):
        capabilities = {
            "indexers": {
                "stale": {
                    "parity_eligible": False,
                    "movie_imdbid": {"state": "honoured"},
                },
                "current": {
                    "parity_eligible": True,
                    "movie_imdbid": {"state": "honoured"},
                },
            }
        }

        self.assertEqual(titles.parity_indexers(capabilities), ["current"])
        self.assertEqual(
            titles.counting_indexers(capabilities)["movie"], ["current"]
        )


class TitleBuildTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for module, paths in (
            (census, {"OUT": "availability.json", "ARCHIVE": "availability.7z"}),
            (titles, {"CANDIDATES": "candidates.json", "CAPABILITIES": "capabilities.json",
                      "OUT": "titles.json"}),
        ):
            patcher = mock.patch.multiple(module, **{
                key: str(self.root / filename) for key, filename in paths.items()
            })
            patcher.start()
            self.addCleanup(patcher.stop)
        self.write("candidates.json", {"candidates": [{
            "id": "tt0000001", "type": "movie", "title": "Matrix", "proposed_tier": "thin",
        }]})
        self.capabilities = {
            "measured_utc": "2026-09-07T00:00:00Z",
            "indexers": {
                "indexer-a": {"parity_eligible": True, "movie_imdbid": {"state": "honoured"}},
                "indexer-b": {"parity_eligible": True, "movie_imdbid": {"state": "honoured"}},
                "excluded": {"parity_eligible": False, "movie_imdbid": {"state": "honoured"}},
            },
        }
        self.write("capabilities.json", self.capabilities)
        self.rows = {
            "indexer-a": {"total": 9, "error": None,
                          "kept": [{"name": "Matrix.remux", "size": 9 * 1024**3}] * 8,
                          "sizes": [9 * 1024**3] * 8 + [1024**3]},
            "indexer-b": {"total": 0, "error": None, "kept": [], "sizes": []},
        }

    def write(self, filename, document):
        (self.root / filename).write_text(json.dumps(document))

    def build_entry(self):
        self.write("availability.json", {
            "measured_utc": "2026-09-07T00:00:00Z", "results": {"tt0000001": self.rows},
        })
        return titles.build()["titles"][0]

    def test_playable_release_beyond_kept_names_is_counted(self):
        entry = self.build_entry()
        self.assertEqual(entry["size_band"], "has-playable")
        self.assertEqual(entry["size_band_from"], 9)

    def test_excluded_indexer_cannot_supply_playable_size(self):
        self.rows["indexer-a"]["sizes"] = [9 * 1024**3]
        self.rows["excluded"] = {"total": 1, "error": None, "kept": [],
                                 "sizes": [1024**3]}
        entry = self.build_entry()
        self.assertEqual(entry["size_band"], "oversize-only")
        self.assertEqual(entry["size_band_from"], 1)
        self.assertEqual(entry["reachable_results"], 9)

    def test_missing_page_sizes_are_not_inferred_from_kept_names(self):
        del self.rows["indexer-a"]["sizes"]
        entry = self.build_entry()
        self.assertEqual(entry["size_band"], "unknown")
        self.assertEqual(entry["size_band_from"], 0)

    def test_invalid_sizes_do_not_count_as_measurements(self):
        self.rows["indexer-a"]["sizes"] = [None, 0, -1]
        entry = self.build_entry()
        self.assertEqual(entry["size_band"], "unknown")
        self.assertEqual(entry["size_band_from"], 0)

    def test_missing_or_failed_parity_row_excludes_partial_measurement(self):
        for row in (None, {"total": None, "error": "http 429", "kept": []},
                    {"total": None, "error": None, "kept": []}):
            with self.subTest(row=row):
                if row is None:
                    self.rows.pop("indexer-b", None)
                else:
                    self.rows["indexer-b"] = row
                entry = self.build_entry()
                self.assertEqual(entry["measured_tier"], "incomplete")
                self.assertFalse(entry["census_complete"])
                self.assertFalse(entry["in_round"])
                self.assertIsNone(entry["expected_outcome"])
                self.assertEqual(entry["parity_errors"], ["indexer-b"])

    def test_unmeasured_title_is_not_absent(self):
        self.rows = {}
        entry = self.build_entry()
        self.assertEqual(entry["measured_tier"], "incomplete")
        self.assertFalse(entry["in_round"])
        self.assertEqual(entry["parity_errors"], ["indexer-a", "indexer-b"])

    def test_complete_zero_results_are_absent(self):
        self.rows["indexer-a"] = {"total": 0, "error": None, "kept": [], "sizes": []}
        entry = self.build_entry()
        self.assertEqual(entry["measured_tier"], "absent")
        self.assertTrue(entry["census_complete"])
        self.assertTrue(entry["in_round"])
        self.assertEqual(entry["expected_outcome"], "empty-list")

    def test_no_capable_indexer_is_not_an_incomplete_title(self):
        for indexer in self.capabilities["indexers"].values():
            indexer["movie_imdbid"]["state"] = "unsupported"
        self.write("capabilities.json", self.capabilities)
        self.rows = {}
        entry = self.build_entry()
        self.assertEqual(entry["measured_tier"], "no-capable-indexer")
        self.assertFalse(entry["in_round"])
        self.assertEqual(entry["parity_errors"], [])

    def test_incomplete_measurement_precedes_mismatch_classification(self):
        self.rows["indexer-a"]["kept"] = [{"name": "Unrelated.film"}]
        self.rows.pop("indexer-b")
        entry = self.build_entry()
        self.assertEqual(entry["title_match"], "MISMATCH")
        self.assertEqual(entry["measured_tier"], "incomplete")
        self.assertFalse(entry["in_round"])


class SizeBandTests(unittest.TestCase):
    def test_playable_bounds_are_inclusive(self):
        for size in (titles.MIN_FEATURE_BYTES, titles.PLAYABLE_CAP_BYTES):
            with self.subTest(size=size):
                self.assertEqual(titles.size_band([size]), "has-playable")

    def test_outside_playable_bounds(self):
        self.assertEqual(titles.size_band([titles.MIN_FEATURE_BYTES - 1]), "undersized-only")
        self.assertEqual(titles.size_band([titles.PLAYABLE_CAP_BYTES + 1]), "oversize-only")
        self.assertEqual(titles.size_band([]), "unknown")


if __name__ == "__main__":
    unittest.main()
