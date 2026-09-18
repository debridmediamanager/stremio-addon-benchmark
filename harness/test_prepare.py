#!/usr/bin/env python3
import json
import os
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class StremThruIsolationTests(unittest.TestCase):
    def test_usenet_benchmark_disables_unrelated_hashlist_worker(self):
        fixture = os.path.join(ROOT, "fixtures", "stremthru-background-worker.json")
        with open(fixture) as handle:
            observed = json.load(handle)
        self.assertEqual("worker/sync-dmm-hashlist", observed["scope"])

        with open(os.path.join(ROOT, "harness", "prepare-current.sh")) as handle:
            script = handle.read()
        self.assertIn(
            'STREMTHRU_FEATURE: "newz,stremio_newz,vault"',
            script,
        )

    def test_retired_state_is_outside_each_measured_target_directory(self):
        fixture = os.path.join(
            ROOT, "fixtures", "stremthru-contaminated-state-disk.json"
        )
        with open(fixture) as handle:
            observed = json.load(handle)
        self.assertGreater(observed["state_disk_start_MB"], 16000)

        with open(os.path.join(ROOT, "harness", "prepare-current.sh")) as handle:
            script = handle.read()
        self.assertIn('RETIRE_ROOT="${RETIRE_ROOT:-$RUN/.retired/$STAMP}"', script)
        self.assertIn('destination="$RETIRE_ROOT/${path#"$RUN"/}"', script)


if __name__ == "__main__":
    unittest.main()
