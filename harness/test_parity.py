#!/usr/bin/env python3
import os
import tempfile
import unittest

import parity


class ParitySamplesTests(unittest.TestCase):
    def test_ignores_local_ephemeral_port_that_contains_563(self):
        content = """1789694542
host 0 0 10.0.0.2:22 10.0.0.3:56375 users:((\"sshd\",pid=8,fd=4))
sab-zurg 0 0 172.1.0.2:44000 23.1.0.2:563
"""
        with tempfile.NamedTemporaryFile("w", delete=False) as handle:
            handle.write(content)
            path = handle.name
        try:
            self.assertEqual(list(parity.samples(path)), [(1789694542, "sab-zurg", None)])
        finally:
            os.unlink(path)

    def test_exact_budget_requires_every_target_to_reach_ten(self):
        self.assertTrue(parity.exact_budget({"zurg": 10, "streamnzb": 10}, 10))
        self.assertFalse(parity.exact_budget({"zurg": 10, "streamnzb": 9}, 10))


if __name__ == "__main__":
    unittest.main()
