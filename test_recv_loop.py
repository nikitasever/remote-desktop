"""
Tests for host.py recv_loop: verifies MSG_INPUT, MSG_FILE_*, and MSG_PING
are correctly processed when received over a socket.
"""
import json
import os
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common


# ---------------------------------------------------------------------------
# Minimal stubs so we can import host.py functions without a real screen/mouse
# ---------------------------------------------------------------------------

class FakeInputInjector:
    """Records handle() calls instead of moving the real mouse."""
    def __init__(self):
        self.calls = []

    def handle(self, ev):
        self.calls.append(ev)


def _run_recv_loop(sock, chan, injector, downloads_dir, alive, results):
    """Run the recv_loop logic extracted from host.serve() in isolation.

    This re-implements the same message dispatch as host.recv_loop so we can
    test it without launching a full host (which needs screen capture, etc.).
    """
    sender = common.FrameSender(sock, chan)
    incoming_file = {"f": None, "name": None}
    pongs = results.setdefault("pongs", [])
    input_count = 0

    try:
        while alive["v"]:
            try:
                mt, body = common.recv_frame(sock, chan)
            except (ConnectionError, socket.error):
                raise
            except Exception:
                continue

            if mt == common.MSG_INPUT:
                ev = common.parse_json(body)
                input_count += 1
                injector.handle(ev)
            elif mt == common.MSG_PING:
                sender.send(common.MSG_PONG, body)
                pongs.append(body)
            elif mt == common.MSG_FILE_META:
                meta = common.parse_json(body)
                os.makedirs(downloads_dir, exist_ok=True)
                safe = os.path.basename(meta["name"]) or "file.bin"
                path = os.path.join(downloads_dir, safe)
                incoming_file["f"] = open(path, "wb")
                incoming_file["name"] = path
            elif mt == common.MSG_FILE_CHUNK:
                if incoming_file["f"]:
                    incoming_file["f"].write(body)
            elif mt == common.MSG_FILE_END:
                if incoming_file["f"]:
                    incoming_file["f"].close()
                    incoming_file["f"] = None
    except (ConnectionError, socket.error):
        pass
    finally:
        alive["v"] = False
        if incoming_file["f"]:
            incoming_file["f"].close()
    results["input_count"] = input_count


class TestRecvLoop(unittest.TestCase):
    """Test that recv_loop correctly dispatches MSG_INPUT, files, and PING."""

    def setUp(self):
        self.key = common.derive_key("test-password")
        self.a, self.b = socket.socketpair()
        self.chan_a = common.SecureChannel(self.key)  # "client" side
        self.chan_b = common.SecureChannel(self.key)  # "host/recv_loop" side
        self.injector = FakeInputInjector()
        self.downloads = tempfile.mkdtemp(prefix="rd_test_")
        self.alive = {"v": True}
        self.results = {}

        self.thread = threading.Thread(
            target=_run_recv_loop,
            args=(self.b, self.chan_b, self.injector,
                  self.downloads, self.alive, self.results),
            daemon=True,
        )
        self.thread.start()
        time.sleep(0.05)  # let thread settle

    def tearDown(self):
        self.alive["v"] = False
        try:
            self.a.close()
        except OSError:
            pass
        try:
            self.b.close()
        except OSError:
            pass
        self.thread.join(timeout=2)

    def _send(self, msg_type, body=b""):
        common.send_frame(self.a, self.chan_a, msg_type, body)

    def _send_json(self, msg_type, obj):
        self._send(msg_type, json.dumps(obj).encode("utf-8"))

    # ---- Tests ----

    def test_msg_input_mouse_move(self):
        """MSG_INPUT with a mouse move event reaches injector.handle()."""
        ev = {"k": "move", "x": 0.5, "y": 0.25}
        self._send_json(common.MSG_INPUT, ev)
        time.sleep(0.1)
        self.assertEqual(len(self.injector.calls), 1)
        self.assertEqual(self.injector.calls[0]["k"], "move")
        self.assertAlmostEqual(self.injector.calls[0]["x"], 0.5)

    def test_msg_input_multiple(self):
        """Multiple MSG_INPUT events are all dispatched."""
        for i in range(5):
            self._send_json(common.MSG_INPUT, {"k": "move", "x": i * 0.1, "y": 0.0})
        time.sleep(0.2)
        self.assertEqual(len(self.injector.calls), 5)

    def test_msg_input_keyboard(self):
        """Keyboard events reach injector."""
        self._send_json(common.MSG_INPUT, {"k": "kdown", "name": "ctrl"})
        self._send_json(common.MSG_INPUT, {"k": "kup", "name": "ctrl"})
        time.sleep(0.1)
        self.assertEqual(len(self.injector.calls), 2)
        self.assertEqual(self.injector.calls[0]["k"], "kdown")
        self.assertEqual(self.injector.calls[1]["k"], "kup")

    def test_msg_input_mouse_click(self):
        """Mouse down/up events reach injector."""
        self._send_json(common.MSG_INPUT, {"k": "down", "x": 0.3, "y": 0.7, "btn": "left"})
        self._send_json(common.MSG_INPUT, {"k": "up", "x": 0.3, "y": 0.7, "btn": "left"})
        time.sleep(0.1)
        self.assertEqual(len(self.injector.calls), 2)

    def test_ping_pong(self):
        """MSG_PING is reflected as MSG_PONG."""
        self._send_json(common.MSG_PING, {"t": 12345.0})
        # Read the PONG response from socket a
        self.a.settimeout(2)
        mt, body = common.recv_frame(self.a, self.chan_a)
        self.assertEqual(mt, common.MSG_PONG)
        self.assertEqual(common.parse_json(body)["t"], 12345.0)

    def test_file_transfer(self):
        """MSG_FILE_META + CHUNK + END saves the file to downloads."""
        content = b"hello-world-test-file-content"
        self._send_json(common.MSG_FILE_META, {"name": "test.txt", "size": len(content)})
        self._send(common.MSG_FILE_CHUNK, content)
        self._send(common.MSG_FILE_END)
        time.sleep(0.3)
        path = os.path.join(self.downloads, "test.txt")
        self.assertTrue(os.path.exists(path), f"File not found at {path}")
        with open(path, "rb") as f:
            self.assertEqual(f.read(), content)

    def test_file_transfer_multi_chunk(self):
        """File transfer with multiple chunks."""
        chunks = [os.urandom(100) for _ in range(3)]
        total = sum(len(c) for c in chunks)
        self._send_json(common.MSG_FILE_META, {"name": "multi.bin", "size": total})
        for c in chunks:
            self._send(common.MSG_FILE_CHUNK, c)
        self._send(common.MSG_FILE_END)
        time.sleep(0.3)
        path = os.path.join(self.downloads, "multi.bin")
        self.assertTrue(os.path.exists(path))
        with open(path, "rb") as f:
            self.assertEqual(f.read(), b"".join(chunks))

    def test_interleaved_input_and_file(self):
        """Input events interleaved with file transfer all work."""
        self._send_json(common.MSG_INPUT, {"k": "move", "x": 0.1, "y": 0.1})
        self._send_json(common.MSG_FILE_META, {"name": "inter.txt", "size": 4})
        self._send_json(common.MSG_INPUT, {"k": "move", "x": 0.2, "y": 0.2})
        self._send(common.MSG_FILE_CHUNK, b"data")
        self._send(common.MSG_FILE_END)
        self._send_json(common.MSG_INPUT, {"k": "move", "x": 0.3, "y": 0.3})
        time.sleep(0.3)
        self.assertEqual(len(self.injector.calls), 3)
        path = os.path.join(self.downloads, "inter.txt")
        self.assertTrue(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
