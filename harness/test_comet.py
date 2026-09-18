#!/usr/bin/env python3
import os
import sys
import unittest
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "standup"))
import comet  # noqa: E402


class CometConfigTests(unittest.TestCase):
    def test_persisted_configuration_ids_are_uuids(self):
        config = comet.build_config(
            [{"label": "indexer-d", "url": "https://example.test", "api_key": "x"}],
            "token",
        )

        uuid.UUID(config["discoverySources"][0]["configurationId"])
        uuid.UUID(config["playbackProviders"][0]["configurationId"])


if __name__ == "__main__":
    unittest.main()
