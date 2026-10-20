"""HTTPS policy checks with temporary certificates and loopback-only endpoints."""

import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import os
from pathlib import Path
import socket
import ssl
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.error

from https_transport import https_urlopen, install, require_https, tls_server


def certificate(directory):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "A2A test fixture")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=1))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = directory / "cert.pem", directory / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()))
    key_path.chmod(0o600)
    return str(cert_path), str(key_path)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/never-contact")
            self.end_headers()
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"TLS verified")


class HTTPSChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="a2a-tls-")
        self.addCleanup(self.tmp.cleanup)
        self.cert, self.key = certificate(Path(self.tmp.name))
        secure_server = tls_server(ThreadingHTTPServer, self.cert, self.key)
        with patch.dict(os.environ, {"A2A_PUBLIC_URL": "https://127.0.0.1"}):
            self.server = secure_server(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.url = f"https://127.0.0.1:{self.server.server_port}"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def test_valid_tls_and_required_certificate_verification(self):
        with https_urlopen(self.cert)(self.url, timeout=3) as response:
            self.assertEqual(response.read(), b"TLS verified")
        with self.assertRaises(urllib.error.URLError):
            https_urlopen("")(self.url, timeout=3)
        with self.assertRaises(urllib.error.URLError):
            https_urlopen(self.cert)(self.url.replace("127.0.0.1", "localhost"), timeout=3)

    def test_http_and_downgrade_redirect_are_rejected(self):
        with self.assertRaises(ValueError):
            https_urlopen(self.cert)(self.url.replace("https:", "http:"))
        with self.assertRaises(urllib.error.HTTPError):
            https_urlopen(self.cert)(self.url + "/redirect", timeout=3)
        with socket.create_connection(("127.0.0.1", self.server.server_port), timeout=3) as client:
            client.sendall(b"GET / HTTP/1.0\r\n\r\n")
            try:
                self.assertEqual(client.recv(100), b"")
            except ConnectionResetError:
                pass

    def test_bad_urls_and_missing_tls_material_fail_closed(self):
        for url in ("http://peer", "ftp://peer", "https://user:pass@peer", "https:///missing"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                require_https(url)
        with patch.dict(os.environ, {"A2A_PUBLIC_URL": self.url}), self.assertRaises(ValueError):
            tls_server(ThreadingHTTPServer, "", "")(("127.0.0.1", 0), Handler)

    def test_agent_card_cannot_change_origin_or_downgrade(self):
        tools = SimpleNamespace(_rpc_url=lambda base, card: card["url"], _SCHEMAS={})
        adapter = SimpleNamespace(_A2AServer=ThreadingHTTPServer)
        install(adapter, tools, {"cert_file": self.cert, "key_file": self.key, "ca_file": self.cert})
        self.assertEqual(tools._rpc_url(self.url, {"url": self.url + "/rpc"}), self.url + "/rpc")
        for url in ("http://127.0.0.1:1", "https://other.example", "https://127.0.0.1:1"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                tools._rpc_url(self.url, {"url": url})
        self.assertIsNot(adapter.urllib, __import__("urllib"))

    def test_default_profile_can_have_a_path_prefix(self):
        class Adapter:
            def _load_served_agents(self, extra):
                return {"": {"path": "", "local": True}}
        module = SimpleNamespace(_A2AServer=ThreadingHTTPServer, A2AAdapter=Adapter)
        tools = SimpleNamespace(_rpc_url=lambda base, card: base, _SCHEMAS={})
        install(module, tools, {"path_prefix": "default"})
        self.assertEqual(Adapter()._load_served_agents({}), {"": {"path": "/default", "local": True}})


if __name__ == "__main__":
    unittest.main()
