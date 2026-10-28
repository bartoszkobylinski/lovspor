"""The observatory's TLS terms (issue #508).

The operator's question: does every observatory client offer the two
RSA-key-exchange AEAD suites a TLS-1.2-only server needs, after everything
Python offers by default, and nothing else that the default refuses?
"""

from __future__ import annotations

import ast
import ssl
from pathlib import Path

import httpx
import pytest

from lovspor.observatory import tls

SRC = Path(__file__).resolve().parents[2] / "src" / "lovspor" / "observatory"
#: The command modules that open HTTP connections to municipal servers.
CONNECTING_MODULES = ("commands.py", "registry_commands.py", "survey_commands.py")


def _names(context: ssl.SSLContext, protocol: str | None = None) -> list[str]:
    return [c["name"] for c in context.get_ciphers() if protocol in (None, c["protocol"])]


class TestContext:
    def test_the_default_suites_come_first_in_their_order(self) -> None:
        default = httpx.create_ssl_context()

        found = tls.observatory_ssl_context()

        assert _names(found)[: len(_names(default))] == _names(default)

    def test_the_rsa_key_exchange_aead_suites_come_last_and_only_those(self) -> None:
        found = tls.observatory_ssl_context()
        default = set(_names(httpx.create_ssl_context()))

        added = [name for name in _names(found) if name not in default]

        assert added == list(tls.RSA_KEY_EXCHANGE_SUITES)
        assert _names(found)[-2:] == list(tls.RSA_KEY_EXCHANGE_SUITES)
        rsa = [c for c in found.get_ciphers() if c["kea"] == "kx-rsa"]
        assert {c["name"] for c in rsa} == set(tls.RSA_KEY_EXCHANGE_SUITES)
        assert all("GCM" in c["name"] and c["aead"] for c in rsa)

    def test_tls_1_3_and_the_minimum_version_are_untouched(self) -> None:
        default = httpx.create_ssl_context()

        found = tls.observatory_ssl_context()

        assert _names(found, "TLSv1.3") == _names(default, "TLSv1.3")
        assert found.minimum_version == default.minimum_version == ssl.TLSVersion.TLSv1_2
        assert (found.verify_mode, found.check_hostname) == (ssl.CERT_REQUIRED, True)

    def test_the_client_carries_the_context_and_the_options(self) -> None:
        client = tls.observatory_client(timeout=7.0)

        assert client.timeout == httpx.Timeout(7.0)
        client.close()


@pytest.mark.parametrize("module", CONNECTING_MODULES)
def test_every_connecting_command_module_uses_the_observatory_client(module: str) -> None:
    tree = ast.parse((SRC / module).read_text(encoding="utf-8"))
    bare = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "Client"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "httpx"
    ]
    assert bare == [], f"{module} builds httpx.Client directly at lines {bare}"
    assert "observatory_client" in (SRC / module).read_text(encoding="utf-8")
