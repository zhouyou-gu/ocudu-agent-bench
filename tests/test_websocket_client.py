import base64
import hashlib
import socket
import struct
import threading
import time
import unittest

from benchmark.benchmark_api.websocket_client import WebSocketClient, WebSocketProtocolError


def recv_exact(conn, count):
    data = b""
    while len(data) < count:
        chunk = conn.recv(count - len(data))
        if not chunk:
            raise EOFError("unexpected EOF")
        data += chunk
    return data


def read_client_text(conn):
    first, second = recv_exact(conn, 2)
    length = second & 0x7F
    if length == 126:
        length = struct.unpack("!H", recv_exact(conn, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", recv_exact(conn, 8))[0]
    mask = recv_exact(conn, 4)
    payload = recv_exact(conn, length)
    return bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload)).decode("utf-8")


def server_frame_bytes(opcode, payload):
    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    if len(data) < 126:
        header = struct.pack("!BB", 0x80 | opcode, len(data))
    else:
        header = struct.pack("!BBH", 0x80 | opcode, 126, len(data))
    return header + data


def send_server_frame(conn, opcode, payload):
    conn.sendall(server_frame_bytes(opcode, payload))


class FakeWebSocketServer:
    def __init__(self, handler, handshake_tail=b""):
        self.handler = handler
        self.handshake_tail = handshake_tail
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.port = None

    def __enter__(self):
        self.thread.start()
        self.ready.wait(timeout=5)
        return self

    def __exit__(self, exc_type, exc, tb):
        self.thread.join(timeout=5)

    def _run(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            self.port = server.getsockname()[1]
            self.ready.set()
            conn, _ = server.accept()
            with conn:
                request = b""
                while b"\r\n\r\n" not in request:
                    request += conn.recv(4096)
                headers = {}
                for line in request.decode("iso-8859-1").split("\r\n")[1:]:
                    if ":" in line:
                        name, value = line.split(":", 1)
                        headers[name.lower()] = value.strip()
                key = headers["sec-websocket-key"]
                accept = base64.b64encode(
                    hashlib.sha1((key + WebSocketClient.GUID).encode("ascii")).digest()
                ).decode("ascii")
                response = (
                    "HTTP/1.1 101 Switching Protocols\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    f"Sec-WebSocket-Accept: {accept}\r\n"
                    "\r\n"
                ).encode("ascii")
                conn.sendall(response + self.handshake_tail)
                self.handler(conn)


class WebSocketClientTests(unittest.TestCase):
    def test_client_sends_and_receives_text(self) -> None:
        def handler(conn):
            self.assertEqual(read_client_text(conn), "hello")
            send_server_frame(conn, 0x1, '{"ok": true}')

        with FakeWebSocketServer(handler) as server:
            with WebSocketClient("127.0.0.1", server.port, timeout=2.0) as client:
                client.send_text("hello")
                self.assertEqual(client.recv_text(timeout=2.0), '{"ok": true}')

    def test_client_rejects_unsupported_binary_frame(self) -> None:
        def handler(conn):
            time.sleep(0.1)

        with FakeWebSocketServer(handler, handshake_tail=server_frame_bytes(0x2, b"\x00\x01")) as server:
            with WebSocketClient("127.0.0.1", server.port, timeout=2.0) as client:
                with self.assertRaisesRegex(WebSocketProtocolError, "Unsupported WebSocket opcode"):
                    client.recv_text(timeout=2.0)


if __name__ == "__main__":
    unittest.main()
