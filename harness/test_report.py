#!/usr/bin/env python3
import unittest
from unittest.mock import patch

import report


class NoiseRoundTests(unittest.TestCase):
    def test_combines_independent_rounds_as_repeat_passes(self):
        documents = {
            "pass-a": {"zurg": {"target": "zurg", "passes": [[
                {"id": "movie", "click_to_byte_s": 1.0}
            ]]}},
            "pass-b": {"zurg": {"target": "zurg", "passes": [[
                {"id": "movie", "click_to_byte_s": 1.3}
            ]]}},
        }
        with patch.object(report, "load", side_effect=lambda name: documents[name]):
            combined = report.combine_noise_rounds(["pass-a", "pass-b"])
        self.assertEqual(2, len(combined["zurg"]["passes"]))
        self.assertEqual(0.3, report.noise_floor(combined["zurg"])["median_spread_s"])

    def test_metric_rankings_order_each_measure_on_its_own_direction(self):
        render = getattr(report, "render_metric_rankings", lambda summaries: [])
        rows = [
            {"target": "zurg", "coverage_pct": 50.0,
             "median_population_c2b_s": 2.0, "median_served_c2b_s": 1.0,
             "median_stream_list_s": 0.2, "median_resolve_s": 0.8,
             "median_ttfb_s": 0.8, "median_throughput_mb_s": 8.0,
             "median_p05_window_mb_s": 4.0, "sustain_25mbps_n": 4,
             "verdicts": {"correct": 8}, "over_cap_titles": 1,
             "picked_over_cap": 0},
            {"target": "other", "coverage_pct": 75.0,
             "median_population_c2b_s": 4.0, "median_served_c2b_s": 3.0,
             "median_stream_list_s": 0.4, "median_resolve_s": 2.6,
             "median_ttfb_s": 2.6, "median_throughput_mb_s": 6.0,
             "median_p05_window_mb_s": 2.0, "sustain_25mbps_n": 2,
             "verdicts": {"correct": 7}, "over_cap_titles": 2,
             "picked_over_cap": 1},
        ]
        text = "\n".join(render(rows))
        self.assertIn("Coverage (higher is better): 1. other", text)
        self.assertIn("Median throughput (higher is better): 1. zurg", text)
        self.assertIn("Population click-to-byte (lower is better): 1. zurg", text)


class SampleOutcomeTests(unittest.TestCase):
    """A sample is a product decision, not a defect and not a score.

    The shape is a real one, measured on zen 2026-09-20: the corpus release
    Father.Brown.2013.S02E05.HDTV.x264-TLA is a 12-volume scene RAR whose
    archive lists a 9,538,724-byte sample before its 321,905,886-byte feature,
    and a target that opens the first video it finds serves the sample. It
    arrives whole and valid, and it ends long before a read window closes, so
    it used to be recorded as `truncated` -- a defect it is not.
    """

    def row(self, served, release, outcome="truncated"):
        return {"id": "tt0017136", "expected_outcome": "playable-stream",
                "outcome": outcome, "content_bytes_total": served,
                "chosen": {"size_bytes": release}, "n_streams": 5,
                "click_to_byte_s": 2.0, "throughput_mb_s": 9.0}

    def test_a_sample_is_reread_out_of_truncated(self):
        # 9.5 MB of a 322 MB release: the real FatherBrown geometry.
        self.assertEqual("sample", report.outcome_of(self.row(9_538_724, 321_905_886)))

    def test_the_feature_is_untouched(self):
        self.assertEqual("served", report.outcome_of(
            self.row(321_905_886, 321_905_886, outcome="served")))

    def test_an_error_clip_is_still_an_error_clip(self):
        # Under a megabyte stays a placeholder: a 19 KB status clip is not a
        # sample of anything, and the two findings must not merge.
        self.assertEqual("placeholder", report.outcome_of(self.row(19_000, 321_905_886)))

    def test_a_genuine_truncation_is_still_a_truncation(self):
        # Most of the release arrived and then stopped. Nothing about that is
        # a choice of file, and it stays a defect.
        self.assertEqual("truncated", report.outcome_of(self.row(300_000_000, 321_905_886)))

    def test_a_sample_is_judged_neither_way(self):
        self.assertEqual("sample", report.verdict(self.row(9_538_724, 321_905_886)))

    def test_a_sample_leaves_the_scored_population(self):
        document = {"target": "zurg", "population": 3, "passes": [[
            self.row(9_538_724, 321_905_886),
            self.row(321_905_886, 321_905_886, outcome="served"),
            {"id": "gone", "expected_outcome": "playable-stream",
             "outcome": "resolve-failed", "n_streams": 2},
        ]]}
        got = report.summarise("zurg", document, cap_bytes=6 * 1024 ** 3)
        self.assertEqual(1, got["sample"])
        self.assertEqual(1, got["served"])
        # Three titles asked, two scored. The asked count is what the
        # same-set guard and the budget warning are about, and a row set aside
        # after measurement is neither of those; the scored count is the
        # denominator, so the sample is out of the numerator and the
        # denominator alike and moves coverage in neither direction.
        self.assertEqual(3, got["population"])
        self.assertEqual(2, got["scored"])
        self.assertEqual(50.0, got["coverage_pct"])
        self.assertEqual(0, got["verdicts"].get("partial", 0),
                         "the sample must not land in the truncation tier")
        self.assertEqual(1, got["verdicts"].get("missed", 0),
                         "the genuine failure is still counted")


if __name__ == "__main__":
    unittest.main()
