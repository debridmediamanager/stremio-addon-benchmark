#!/usr/bin/env python3
"""Offline tests for per-target CPU, memory and disk accounting."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import resources  # noqa: E402


class ResourceTests(unittest.TestCase):
    def test_disk_usage_counts_allocated_target_state(self):
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, "state.bin"), "wb") as handle:
                handle.write(b"x" * 8192)
            self.assertGreaterEqual(resources.disk_usage_bytes(directory), 8192)

    def test_summary_keeps_cpu_memory_io_and_state_growth_separate(self):
        samples = [(float(i), 0.5, 200 * 1024) for i in range(10)]
        result = resources.summarise(
            samples, cpu_s=5.0, read_bytes=64 << 20, write_bytes=8 << 20,
            disk_start=4 << 20, disk_end=10 << 20)
        self.assertEqual(result["cpu_s"], 5.0)
        self.assertEqual(result["rss_peak_MB"], 200.0)
        self.assertEqual(result["disk_read_MB"], 64.0)
        self.assertEqual(result["disk_write_MB"], 8.0)
        self.assertEqual(result["state_disk_delta_MB"], 6.0)


if __name__ == "__main__":
    unittest.main()
