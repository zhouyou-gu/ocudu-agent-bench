"""Small stdlib-only WebSocket text client for conformance probes."""

from __future__ import annotations

import base64
import hashlib
import os
import socket
import struct
from dataclasses import dataclass


class WebSocketProtocolError(RuntimeError):
    """Raised when a WebSocket peer violates the minimal protocol we need."""


@dataclass
class WebSocketFrame:
    opcode: int
    payload: bytes


class WebSocketClient:
    GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

    def __init__(self, host: str, port: int, path: str = "/", timeout: float = 10.0) -> None:
        self.host = host
        self.port = port
        self.path = path
        self.timeout = timeout
        self.sock: socket.socket | None = None
        self._recv_buffer = b""

    def __enter__(self) -> "WebSocketClient":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def connect(self) -> None:
        sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        sock.settimeout(self.timeout)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        sock.sendall(request.encode("ascii"))
        response = self._read_http_response(sock)
        lines = response.split("\r\n")
        if not lines or " 101 " not in lines[0]:
            raise WebSocketProtocolError(f"WebSocket upgrade failed: {lines[0] if lines else '<empty response>'}")
        headers = {}
        for line in lines[1:]:
            if ":" not in line:
                continue
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()
        expected_accept = base64.b64encode(hashlib.sha1((key + self.GUID).encode("ascii")).digest()).decode("ascii")
        if headers.get("sec-websocket-accept") != expected_accept:
            raise WebSocketProtocolError("WebSocket upgrade returned an invalid Sec-WebSocket-Accept header")
        self.sock = sock

    def send_text(self, text: str) -> None:
        self._send_frame(0x1, text.encode("utf-8"))

    def recv_text(self, timeout: float | None = None) -> str | None:
        sock = self._require_socket()
        old_timeout = sock.gettimeout()
        if timeout is not None:
            sock.settimeout(timeout)
        try:
            while True:
                frame = self._recv_frame()
                if frame.opcode == 0x8:
                    return None
                if frame.opcode == 0x9:
                    self._send_frame(0xA, frame.payload)
                    continue
                if frame.opcode == 0xA:
                    continue
                if frame.opcode != 0x1:
                    raise WebSocketProtocolError(f"Unsupported WebSocket opcode: {frame.opcode}")
                return frame.payload.decode("utf-8")
        finally:
            if timeout is not None:
                sock.settimeout(old_timeout)

    def close(self) -> None:
        if self.sock is None:
            return
        try:
            self._send_frame(0x8, b"")
        except OSError:
            pass
        try:
            self.sock.close()
        finally:
            self.sock = None

    def _require_socket(self) -> socket.socket:
        if self.sock is None:
            raise WebSocketProtocolError("WebSocket is not connected")
        return self.sock

    def _read_http_response(self, sock: socket.socket) -> str:
        chunks = []
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
            data = b"".join(chunks)
            if len(data) > 65536:
                raise WebSocketProtocolError("WebSocket upgrade response is too large")
        header, separator, remainder = data.partition(b"\r\n\r\n")
        if not separator:
            return data.decode("iso-8859-1", errors="replace")
        self._recv_buffer = remainder
        return (header + separator).decode("iso-8859-1", errors="replace")

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        sock = self._require_socket()
        first = 0x80 | opcode
        mask_key = os.urandom(4)
        length = len(payload)
        if length < 126:
            header = struct.pack("!BB", first, 0x80 | length)
        elif length <= 0xFFFF:
            header = struct.pack("!BBH", first, 0x80 | 126, length)
        else:
            header = struct.pack("!BBQ", first, 0x80 | 127, length)
        masked = bytes(byte ^ mask_key[index % 4] for index, byte in enumerate(payload))
        sock.sendall(header + mask_key + masked)

    def _recv_frame(self) -> WebSocketFrame:
        first_two = self._recv_exact(2)
        first, second = first_two
        fin = bool(first & 0x80)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if not fin:
            raise WebSocketProtocolError("Fragmented WebSocket frames are not supported")
        if length == 126:
            length = struct.unpack("!H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv_exact(8))[0]
        mask_key = self._recv_exact(4) if masked else b""
        payload = self._recv_exact(length)
        if masked:
            payload = bytes(byte ^ mask_key[index % 4] for index, byte in enumerate(payload))
        return WebSocketFrame(opcode=opcode, payload=payload)

    def _recv_exact(self, count: int) -> bytes:
        sock = self._require_socket()
        data = b""
        if self._recv_buffer:
            data = self._recv_buffer[:count]
            self._recv_buffer = self._recv_buffer[count:]
        while len(data) < count:
            chunk = sock.recv(count - len(data))
            if not chunk:
                raise WebSocketProtocolError("Unexpected EOF while reading WebSocket frame")
            data += chunk
        return data
