#!/usr/bin/env python3
import unittest

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


if __name__ == "__main__":
    unittest.main()
