"""TLS transport policy; protocol, auth, task routing and persistence stay upstream."""

from functools import wraps
import os
import re
import ssl
from types import SimpleNamespace
import urllib.error
import urllib.parse
import urllib.request


def require_https(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("A2A requires an HTTPS URL without embedded credentials")
    return parsed


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "A2A redirects are disabled", headers, fp)


def https_urlopen(ca_file):
    def open_url(request, timeout=30):
        require_https(request.full_url if isinstance(request, urllib.request.Request) else request)
        context = ssl.create_default_context()
        if ca_file:
            context.load_verify_locations(cafile=ca_file)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=context), NoRedirect(),
        )
        return opener.open(request, timeout=timeout)
    return open_url


def tls_server(server_class, cert_file, key_file):
    class TLSServer(server_class):
        def __init__(self, *args, **kwargs):
            require_https(os.environ.get("A2A_PUBLIC_URL", ""))
            if not cert_file or not key_file:
                raise ValueError("A2A TLS certificate and private key are required")
            self.tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            self.tls_context.minimum_version = ssl.TLSVersion.TLSv1_2
            self.tls_context.load_cert_chain(cert_file, key_file)
            super().__init__(*args, **kwargs)

        def get_request(self):
            connection, address = super().get_request()
            try:
                return self.tls_context.wrap_socket(
                    connection, server_side=True, do_handshake_on_connect=False,
                ), address
            except Exception:
                connection.close()
                raise

        def process_request_thread(self, request, address):
            # Handshakes run in workers so a slow peer cannot block accept().
            try:
                request.settimeout(10)
                request.do_handshake()
                request.settimeout(300)
            except OSError:
                self.shutdown_request(request)
                return
            super().process_request_thread(request, address)

    return TLSServer


def install(adapter, tools, settings):
    if getattr(adapter, "_dispatch_https_only", False):
        return
    prefix = settings.get("path_prefix", "")
    if prefix and not re.fullmatch(r"[a-zA-Z0-9_-]+", prefix):
        raise ValueError("A2A path_prefix must be a single URL segment")
    # Replace only these modules' urllib reference, never the process-global opener.
    transport = SimpleNamespace(
        error=urllib.error, parse=urllib.parse,
        request=SimpleNamespace(Request=urllib.request.Request,
                                urlopen=https_urlopen(settings.get("ca_file"))),
    )
    tools.urllib = transport
    adapter.urllib = transport
    original_rpc_url = tools._rpc_url

    @wraps(original_rpc_url)
    def rpc_url(base_url, card):
        base = require_https(base_url)
        url = original_rpc_url(base_url, card)
        target = require_https(url)
        if (base.hostname, base.port or 443) != (target.hostname, target.port or 443):
            raise ValueError("A2A Agent Card cannot redirect credentials to another origin")
        return url

    tools._rpc_url = rpc_url
    adapter._A2AServer = tls_server(adapter._A2AServer, settings.get("cert_file"), settings.get("key_file"))
    if prefix:
        original_agents = adapter.A2AAdapter._load_served_agents

        @wraps(original_agents)
        def served_agents(self, extra):
            agents = original_agents(self, extra)
            # Upstream reserves the 'default' slug; give its local route a prefix.
            agents[""]["path"] = "/" + prefix
            return agents

        adapter.A2AAdapter._load_served_agents = served_agents
    for schema in tools._SCHEMAS.values():
        for prop in schema["function"].get("parameters", {}).get("properties", {}).values():
            if "description" in prop:
                prop["description"] = prop["description"].replace("http(s)://", "https://").replace("http://", "https://")
    adapter._dispatch_https_only = True
