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


if __name__ == "__main__":
    unittest.main()
