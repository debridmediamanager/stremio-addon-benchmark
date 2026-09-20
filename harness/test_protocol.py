#!/usr/bin/env python3
"""The seek probe, against a server that answers the four ways one can.

Round 3 recorded six of StreamNZB's seeks as zero fill. It answers a range
with a 302, the probe read the redirect's own empty body, and
`looks_like_a_fill` calls an empty body a fill -- so a target doing something
perfectly ordinary was recorded as serving silence. These pin the difference
between "served silence", "served nothing" and "answered something that is
not a body at all".
"""
import http.server
import threading
import unittest

import protocol


class SeekServer(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/redirect"):
            self.send_response(302)
            self.send_header("Location", "/real")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path.startswith("/real"):
            body = b"\x01" * 4096
        elif self.path.startswith("/zeros"):
            body = b"\x00" * 4096
        elif self.path.startswith("/empty"):
            body = b""
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(206)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class SeekProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.HTTPServer(("127.0.0.1", 0), SeekServer)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def probe(self, path):
        import time
        marks = protocol.seek_profile(self.base + path, 40960,
                                      time.monotonic() + 30)
        return marks[0]

    def test_a_redirect_is_followed_rather_than_judged(self):
        got = self.probe("/redirect")
        self.assertEqual(206, got["status"],
                         "the probe must report the range it was redirected to")
        self.assertIs(False, got["zero_fill"],
                      "a target answering a range with a 302 is not serving silence")

    def test_real_bytes_are_not_a_fill(self):
        self.assertIs(False, self.probe("/real")["zero_fill"])

    def test_a_body_of_zeros_is_a_fill(self):
        self.assertIs(True, self.probe("/zeros")["zero_fill"])

    def test_an_empty_body_is_not_called_silence(self):
        got = self.probe("/empty")
        self.assertIsNone(got["zero_fill"],
                          "serving nothing and serving zeros are different findings")
        self.assertEqual("no body", got["note"])

    def test_an_error_is_reported_as_an_error(self):
        got = self.probe("/gone")
        self.assertEqual(404, got["status"])
        self.assertIsNone(got["zero_fill"])
        self.assertEqual("http 404", got["note"])


if __name__ == "__main__":
    unittest.main()
