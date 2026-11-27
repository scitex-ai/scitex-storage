"""Shared fixtures for the scitex_storage unit tests.

The document-sorter tests need a REAL searchable PDF (embedded text layer),
because that is exactly what the extraction path reads -- and NO MOCKS
(PA-306) means we generate a genuine PDF, not a stub. ``searchable_pdf`` is a
dependency-free factory: it hand-writes a minimal single-page PDF whose
content stream draws the given lines with a standard Helvetica font, so
``scitex_io.load(pdf, mode="text")`` (the production read path) extracts them
back verbatim. Text is Latin-1 (ASCII) -- a hand-rolled simple-font PDF cannot
carry CJK glyphs without a CID font, so the Japanese-classification cases are
tested directly on strings in test__classify.py, while the PDF fixture proves
the end-to-end read path.
"""

from __future__ import annotations

import base64
import email
import os
import shutil
import socket
import ssl
import subprocess
import threading
from pathlib import Path

import pytest


def _write_searchable_pdf(path: Path, lines: list[str]) -> Path:
    text_ops = ["BT", "/F1 12 Tf", "72 720 Td"]
    for i, line in enumerate(lines):
        if i:
            text_ops.append("0 -16 Td")
        esc = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        text_ops.append(f"({esc}) Tj")
    text_ops.append("ET")
    content = ("\n".join(text_ops)).encode("latin-1")

    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        (
            b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n"
            + content + b"\nendstream"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    xref_pos = len(out)
    n = len(objs) + 1
    out += b"xref\n0 " + str(n).encode() + b"\n0000000000 65535 f \n"
    for off in offsets:
        out += ("%010d 00000 n \n" % off).encode()
    out += (
        b"trailer\n<< /Size " + str(n).encode() + b" /Root 1 0 R >>\n"
        b"startxref\n" + str(xref_pos).encode() + b"\n%%EOF\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))
    return path


@pytest.fixture
def searchable_pdf():
    """Factory: ``searchable_pdf(path, ["line one", "line two"])`` -> path.

    Writes a genuine single-page PDF with an extractable text layer. Pass
    ``[]`` for a page with no text (the image-only-scan shape)."""
    return _write_searchable_pdf


# --------------------------------------------------------------------------
# A REAL SMTP server on 127.0.0.1 for the alarm email rail (no mocks).
# --------------------------------------------------------------------------


class LocalSMTPServer:
    """Minimal SMTP server (STARTTLS + AUTH PLAIN) recording every message.

    ``refuse=True`` answers RCPT with 550, the shape of a server that will
    not take the message, so the rail's failure path runs for real.
    """

    def __init__(self, certfile: str, keyfile: str, refuse: bool = False):
        self.context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        self.context.load_cert_chain(certfile, keyfile)
        self.refuse = refuse
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        self.listener.settimeout(0.5)
        self.messages: list[dict] = []
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self.listener.accept()
            except (socket.timeout, OSError):
                continue
            try:
                self._session(conn)
            except (OSError, ssl.SSLError):
                pass
        self.listener.close()

    def _session(self, conn) -> None:
        conn.settimeout(10)
        stream = conn.makefile("rb")
        record: dict = {}

        def reply(text: str) -> None:
            conn.sendall(text.encode() + b"\r\n")

        reply("220 localhost ESMTP")
        while True:
            line = stream.readline().decode().rstrip("\r\n")
            verb = line.split(" ", 1)[0].upper()
            if verb in ("EHLO", "HELO"):
                reply("250-localhost\r\n250-AUTH PLAIN\r\n250 STARTTLS")
            elif verb == "STARTTLS":
                reply("220 ready")
                conn = self.context.wrap_socket(conn, server_side=True)
                stream = conn.makefile("rb")
                record["tls"] = True
            elif verb == "AUTH":
                token = line.split()[2]
                parts = base64.b64decode(token).split(b"\0")
                record["login"] = parts[1].decode()
                record["password_ok"] = parts[2].decode() == SMTP_TEST_PASSWORD
                reply("235 ok")
            elif verb == "MAIL":
                record["envelope_from"] = line.split(":", 1)[1].split()[0]
                reply("250 ok")
            elif verb == "RCPT":
                if self.refuse:
                    reply("550 mailbox unavailable")
                    continue
                record["envelope_to"] = line.split(":", 1)[1].strip()
                reply("250 ok")
            elif verb == "DATA":
                reply("354 go")
                data = b""
                while not data.endswith(b"\r\n.\r\n"):
                    data += stream.readline()
                record["message"] = email.message_from_bytes(data[:-5])
                self.messages.append(dict(record))
                reply("250 queued")
            elif verb == "QUIT" or not line:
                reply("221 bye")
                break
            else:
                reply("250 ok")
        conn.close()

    def stop(self) -> None:
        self._stop.set()
        self.thread.join(timeout=5)


#: Test-only credential for the local server; not a real secret.
SMTP_TEST_PASSWORD = "local-test-password"


def _tls_pair(tmp_path: Path) -> tuple[str, str]:
    if shutil.which("openssl") is None:
        pytest.skip("openssl is needed for the local SMTP server's certificate")
    cert, key = tmp_path / "smtp-cert.pem", tmp_path / "smtp-key.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
         "-subj", "/CN=localhost", "-keyout", str(key), "-out", str(cert)],
        check=True,
        capture_output=True,
    )
    return str(cert), str(key)


def _ops_env(tmp_path: Path, port: int) -> dict:
    """The ``ops`` profile variables an alarm host sets, pointed at 127.0.0.1."""
    pw = tmp_path / "no-reply.password"
    pw.write_text(SMTP_TEST_PASSWORD + "\n")
    pw.chmod(0o600)
    return {
        "SCITEX_NOTIFICATION_PROFILE_OPS_FROM": "no-reply@scitex.ai",
        "SCITEX_NOTIFICATION_PROFILE_OPS_TO": "dev@scitex.ai",
        "SCITEX_NOTIFICATION_PROFILE_OPS_SMTP_HOST": "127.0.0.1",
        "SCITEX_NOTIFICATION_PROFILE_OPS_SMTP_PORT": str(port),
        "SCITEX_NOTIFICATION_PROFILE_OPS_PASSWORD_FILE": str(pw),
        "SCITEX_NOTIFICATION_RATE_LIMIT_DIR": str(tmp_path / "rate-limit"),
    }


def _smtp(tmp_path: Path, refuse: bool):
    cert, key = _tls_pair(tmp_path)
    server = LocalSMTPServer(cert, key, refuse=refuse)
    server.env = _ops_env(tmp_path, server.port)
    server.password = SMTP_TEST_PASSWORD
    return server


@pytest.fixture
def smtp_server(tmp_path):
    """A live local SMTP server; ``.env`` holds a matching ``ops`` profile."""
    server = _smtp(tmp_path, refuse=False)
    yield server
    server.stop()


@pytest.fixture
def refusing_smtp_server(tmp_path):
    """A live local SMTP server that refuses every recipient."""
    server = _smtp(tmp_path, refuse=True)
    yield server
    server.stop()


@pytest.fixture
def ops_profile_env(smtp_server):
    """Set the ``ops`` profile in the REAL process environment, then restore."""
    saved = {k: os.environ.get(k) for k in smtp_server.env}
    os.environ.update(smtp_server.env)
    yield smtp_server
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

