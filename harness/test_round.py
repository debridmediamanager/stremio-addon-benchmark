#!/usr/bin/env python3
import importlib.util
import os
import unittest


path = os.path.join(os.path.dirname(__file__), "round.py")
spec = importlib.util.spec_from_file_location("round_harness", path)
round_harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(round_harness)


class SocketAccountingTests(unittest.TestCase):
    def test_only_remote_news_port_counts(self):
        self.assertTrue(round_harness.nntp_peer(
            "0 0 10.0.0.2:41234 10.0.0.3:563 users:((zurg,pid=7,fd=3))"
        ))
        self.assertFalse(round_harness.nntp_peer(
            "0 0 10.0.0.2:45563 10.0.0.3:22 users:((sshd,pid=8,fd=4))"
        ))


if __name__ == "__main__":
    unittest.main()
