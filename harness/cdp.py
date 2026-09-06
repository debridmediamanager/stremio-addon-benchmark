#!/usr/bin/env python3
"""A minimal Chrome DevTools Protocol client, standard library only.

The client plane drives Stremio 4.4, whose shell is Qt WebEngine. Two things
force this file to exist rather than a dependency:

  * **the harness imports only the standard library**, and Python ships no
    WebSocket client. This is RFC 6455 client framing over a plain socket: the
    handshake, masked text frames out, unmasked frames in, and nothing else.
  * **Stremio 4.4 only answers raw CDP.** Playwright's `connect_over_cdp`
    negotiates capabilities its Qt build does not have, and against a browser
    holding many targets it hangs rather than failing.

    from cdp import Browser
    with Browser("127.0.0.1", 9223) as browser:
        page = browser.first_page()
        print(page.evaluate("location.href"))
"""
import base64
import json
import os
import socket
import struct
import time
import urllib.request

TEXT, CLOSE, PING, PONG = 0x1, 0x8, 0x9, 0xA


class WebSocket:
    """Client-side framing. Enough of RFC 6455 to talk to a debugger."""

    def __init__(self, url, timeout=30):
        if not url.startswith("ws://"):
            raise ValueError(f"only ws:// is supported here, got {url[:24]}")
        rest = url[len("ws://"):]
        hostport, _, path = rest.partition("/")
        host, _, port = hostport.partition(":")
        self.sock = socket.create_connection((host, int(port or 80)), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        handshake = (
            f"GET /{path} HTTP/1.1\r\n"
            f"Host: {hostport}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(handshake.encode())
        header = self._read_until(b"\r\n\r\n")
        if b"101" not in header.split(b"\r\n", 1)[0]:
            raise ConnectionError(f"websocket upgrade refused: {header[:120]!r}")
        self.buffer = b""

    def _read_until(self, marker):
        data = b""
        while marker not in data:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("closed during handshake")
            data += chunk
        return data

    def _recv_exactly(self, count):
        while len(self.buffer) < count:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("closed")
            self.buffer += chunk
        out, self.buffer = self.buffer[:count], self.buffer[count:]
        return out

    def send(self, text):
        payload = text.encode()
        header = bytes([0x80 | TEXT])
        length = len(payload)
        if length < 126:
            header += bytes([0x80 | length])
        elif length < (1 << 16):
            header += bytes([0x80 | 126]) + struct.pack(">H", length)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", length)
        mask = os.urandom(4)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self.sock.sendall(header + mask + masked)

    def recv(self):
        while True:
            first, second = self._recv_exactly(2)
            opcode = first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._recv_exactly(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._recv_exactly(8))[0]
            masked = second & 0x80
            mask = self._recv_exactly(4) if masked else b""
            payload = self._recv_exactly(length)
            if masked:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == TEXT:
                return payload.decode("utf-8", "replace")
            if opcode == PING:
                self.sock.sendall(bytes([0x80 | PONG, len(payload)]) + payload)
            elif opcode == CLOSE:
                raise ConnectionError("peer closed the websocket")

    def close(self):
        try:
            self.sock.sendall(bytes([0x80 | CLOSE, 0x80]) + os.urandom(4))
        except OSError:
            pass
        self.sock.close()


class Page:
    """One debuggable target, addressed by its own websocket."""

    def __init__(self, info, timeout=30):
        self.info = info
        self.socket = WebSocket(info["webSocketDebuggerUrl"], timeout=timeout)
        self.next_id = 0

    def call(self, method, params=None, timeout=30):
        self.next_id += 1
        message_id = self.next_id
        self.socket.send(json.dumps({"id": message_id, "method": method,
                                     "params": params or {}}))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = json.loads(self.socket.recv())
            # events arrive interleaved with replies and are ignored here; a
            # caller wanting events should poll state instead, which is what
            # the client plane does anyway (design trap 8)
            if message.get("id") == message_id:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})
        raise TimeoutError(f"{method} did not answer in {timeout}s")

    def evaluate(self, expression, timeout=30, await_promise=False):
        result = self.call("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": await_promise,
        }, timeout=timeout)
        if result.get("exceptionDetails"):
            detail = result["exceptionDetails"]
            text = (detail.get("exception") or {}).get("description") or detail.get("text")
            raise RuntimeError(f"evaluate failed: {str(text)[:300]}")
        return (result.get("result") or {}).get("value")

    def close(self):
        self.socket.close()


class Browser:
    def __init__(self, host="127.0.0.1", port=9223, timeout=30):
        self.host, self.port, self.timeout = host, port, timeout
        self.pages = []

    def targets(self):
        url = f"http://{self.host}:{self.port}/json/list"
        with urllib.request.urlopen(url, timeout=self.timeout) as response:
            return json.loads(response.read().decode())

    def open(self, info):
        page = Page(info, timeout=self.timeout)
        self.pages.append(page)
        return page

    def first_page(self):
        """The app's own page, not a devtools or extension target."""
        for info in self.targets():
            if info.get("type") == "page" and info.get("webSocketDebuggerUrl"):
                return self.open(info)
        raise SystemExit("no debuggable page; is the app running with remote debugging?")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        for page in self.pages:
            page.close()
